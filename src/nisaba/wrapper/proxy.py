"""
Augments injection proxy using mitmproxy.

Intercepts requests to Anthropic API and replaces __NISABA_AUGMENTS_PLACEHOLDER__
with actual augments content. Supports checkpoint-based context compression.
"""

import datetime
import importlib
import json
import logging
import os
import tiktoken


from logging.handlers import RotatingFileHandler
from mitmproxy import http
from nisaba.structured_file import JsonStructuredFile, StructuredFileCache
from nisaba.workspace_files import WorkspaceFiles
from nisaba import session_context
from nisaba.augments import get_augment_manager
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from nisaba.augments import AugmentManager

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Ensure log directory exists
log_dir = Path(".nisaba/logs")
log_dir.mkdir(parents=True, exist_ok=True)

# Add file handler for proxy logs
if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
    file_handler = RotatingFileHandler(
        log_dir / "proxy.log",
        maxBytes=1*1024*1024,  # 10MB
        backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.info("Proxy logging initialized to .nisaba/logs/proxy.log")



class AugmentInjector:
    """
    mitmproxy addon that injects augments content into Anthropic API requests.

    Intercepts POST requests to api.anthropic.com, parses the JSON body,
    finds system prompt blocks containing __NISABA_AUGMENTS_PLACEHOLDER__,
    and replaces:
    - First occurrence: replaced with augments content from file
    - Remaining occurrences: deleted (replaced with empty string)
    """

    def __init__(
        self,
        augment_manager: Optional["AugmentManager"] = None
    ):
        """
        Initialize augments injector.

        Can operate in two modes:
        1. File-based (legacy): reads from augments_file
        2. Shared-state (unified): uses shared AugmentManager

        Args:
            augment_manager: Shared AugmentManager instance (unified mode)
        """
        # Unified mode (shared AugmentManager)
        self.augment_manager = augment_manager

        # Session-specific WorkspaceFiles (lazily initialized per session)
        self._workspace_files_cache: Dict[str, 'WorkspaceFiles'] = {}

        self.filtered_tools:set[str] = { "TodoWrite" }
        self.core_system_prompt_tokens:int = 0
        self.user_id:str|None = None
        self.current_session_id:str|None = None

        # Shared files can be loaded once (no session needed)
        # We'll use a temporary instance just to load these
        # Note: system_prompt and transcript are shared across sessions
        self._shared_system_prompt = StructuredFileCache(
            file_path=Path(".nisaba/base_files/system_prompt.md"),
            name="system prompt",
            tag="USER_SYSTEM_PROMPT_INJECTION"
        )
        self._shared_transcript = StructuredFileCache(
            file_path=Path(".nisaba/base_files/compacted_transcript.md"),
            name="transcript",
            tag="COMPACTED_TRANSCRIPT"
        )

        self._shared_system_prompt.load()
        self._shared_transcript.load()

        if augment_manager:
            logger.info("AugmentInjector initialized in unified mode (shared AugmentManager)")

    def _ensure_main_session_symlink(self) -> None:
        """
        Create/update symlink for main session monitoring.

        Creates: .nisaba/tui → .nisaba/request_cache/{session_id}/tui/

        Only runs in main session (not agent mode).
        Agent mode is detected via NISABA_AGENT_MODE environment variable.
        """
        # Skip in agent mode
        if os.environ.get('NISABA_AGENT_MODE'):
            return

        if not self.current_session_id:
            return

        try:
            symlink = Path(".nisaba/tui")
            target = Path(f".nisaba/request_cache/{self.current_session_id}/tui")

            # Ensure target exists
            target.mkdir(parents=True, exist_ok=True)

            # Check if symlink already points to correct target
            if symlink.is_symlink():
                if symlink.resolve() == target.absolute().resolve():
                    return  # Already correct
                symlink.unlink()  # Remove old symlink
            elif symlink.exists():
                # Not a symlink but exists - remove it
                import shutil
                shutil.rmtree(symlink)

            # Create symlink
            symlink.symlink_to(target.absolute(), target_is_directory=True)
            logger.debug(f"Created session symlink: {symlink} -> {target}")

        except Exception as e:
            logger.warning(f"Failed to create session symlink: {e}")

    def _get_workspace_files(self, session_id: str) -> 'WorkspaceFiles':
        """Get or create WorkspaceFiles instance for session.

        Also bootstraps AugmentManager on first access to ensure:
        - Global pinned augments are loaded
        - augment_view.md is written with initial content
        - augment_state.json is initialized

        Args:
            session_id: Session identifier

        Returns:
            WorkspaceFiles instance for this session
        """
        if not session_id:
            raise ValueError("session_id is required")

        if session_id not in self._workspace_files_cache:
            self._workspace_files_cache[session_id] = WorkspaceFiles.instance(session_id)
            # Bootstrap AugmentManager to initialize session files
            # This loads global pinned augments and writes augment_view.md
            try:
                get_augment_manager(session_id)
                logger.debug(f"Bootstrapped AugmentManager for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to bootstrap AugmentManager for session {session_id}: {e}")
        return self._workspace_files_cache[session_id]

    def request(self, flow: http.HTTPFlow) -> None:
        """
        Intercept and modify requests.

        Called by mitmproxy for each HTTP request. If the request is to
        Anthropic API, we parse the body, replace the placeholder, and
        modify the request.

        In unified mode, also checks for checkpoint and applies compression.

        Args:
            flow: mitmproxy HTTPFlow object
        """
        # Only intercept Anthropic API requests
        if not self._is_anthropic_request(flow):
            return

        try:
            # Parse request body as JSON
            body = json.loads(flow.request.content)

            # if it can't be extracted, it will be at some point.
            try:
                self.user_id = body.get('metadata', {}).get('user_id', None)

                if self.user_id and '_session_' in self.user_id:
                    self.current_session_id = self.user_id.split('_session_')[1]
                    # Set session context for MCP tools
                    session_context.set_current_session(self.current_session_id)
                    # Create symlink for main session (not agent mode)
                    self._ensure_main_session_symlink()
            except Exception as e:
                pass

            # Process system prompt blocks
            if self._inject_augments(body):
                flow.request.content = json.dumps(body).encode('utf-8')
                

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse request JSON: {e}")
        except Exception as e:
            logger.error(f"Error processing request: {e}")

    def _is_anthropic_request(self, flow: http.HTTPFlow) -> bool:
        """
        Check if this is an Anthropic API request.

        Args:
            flow: mitmproxy HTTPFlow object

        Returns:
            True if this is a request to Anthropic API
        """
        return (
            flow.request.method == "POST" and
            "api.anthropic.com" in flow.request.pretty_host
        )

    def _inject_augments(self, body: dict) -> bool:
        """
        Inject augments content into request body.

        Finds __NISABA_AUGMENTS_PLACEHOLDER__ in system blocks:
        - First occurrence: replaced with augments content
        - Remaining occurrences: deleted

        Args:
            body: Parsed JSON request body (modified in place)

        Returns:
            True if any replacements were made
        """
        # If no valid session_id, don't process (safety check)
        if not self.current_session_id:
            logger.warning("No valid session_id, skipping augment injection")
            return False

        # Get session-specific workspace files
        try:
            workspace = self._get_workspace_files(self.current_session_id)
        except Exception as e:
            logger.error(f"Failed to get workspace files for session {self.current_session_id}: {e}")
            return False

        # Filter native tools on every request
        if "tools" in body:
            filtered_tools = []
            for tool in body["tools"]:
                if "name" in tool and tool["name"] in self.filtered_tools:
                    continue
                filtered_tools.append(tool)
            body["tools"] = filtered_tools

        if "system" in body:
            if len(body["system"]) < 2:
                body["system"].append(
                    {
                        "type": "text",
                        "text": (
                            f"\n{self._shared_system_prompt.load()}"
                            f"\n{workspace.augments.load()}"
                            f"\n{self._shared_transcript.load()}"
                        ),
                        "cache_control": {
                            "type": "ephemeral"
                        }
                    }
                )
            elif "text" in body["system"][1]:
                # Generate status bar from current state
                if not workspace.core_system_prompt.file_path.exists() or workspace.core_system_prompt.content != body["system"][1]["text"]:
                    workspace.core_system_prompt.write(body["system"][1]["text"])


                body["system"][1]["text"] = (
                    f"\n{self._shared_system_prompt.load()}"
                    f"\n{workspace.core_system_prompt.load()}"
                    f"\n{workspace.augments.load()}"
                    f"\n{self._shared_transcript.load()}"
                )

        if 'messages' in body and len(body["messages"]) > 2:

            status_bar = f"{self._generate_status_bar(body, workspace)}"

            workspace_text = (
                f"<system-reminder>\n--- WORKSPACE ---"
                f"\n{status_bar}"
                f"\n{workspace.todos.load()}"
                f"\n</system-reminder>"
            )

            body['messages'].append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": workspace_text
                        }
                    ]
                }
            )

            # TODO: this is mostly for development - it needs to bne switched off
            self._write_to_file(Path(os.getcwd()) / '.nisaba/workspace.md', workspace_text, "Workspace markdow written")
            self._write_to_file(Path(os.getcwd()) / '.nisaba/modified_context.json', json.dumps(body, indent=2, ensure_ascii=False), "Modified request written")
            return True

        return "tools" in body or "system" in body

    def _write_to_file(self, file_path:Path, content: str, log_message: str | None  = None) -> None:
        """
        write to file file_path the content optionally displaying log_message
        """
        try:
            # Create/truncate file (only last message)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            if log_message: logger.debug(log_message)

        except Exception as e:
            # Don't crash proxy if logging fails
            logger.error(f"Failed to log context: {e}")
    
    def _create_summary(self, tool_name: str, call: dict, result: dict) -> str:
        """
        Create concise summary from tool result.
        Parses nisaba standard tool return format to extract semantic message.
        
        Args:
            tool_name: Name of the tool
            call: Tool call block (contains input parameters)
            result: Tool result block
            
        Returns:
            Concise summary string
        """
        import re
        
        try:
            content = result.get('content', '')
            
            # Parse JSON response from content
            if isinstance(content, list) and content:
                data = json.loads(content[0].get('text', '{}'))
            elif isinstance(content, str):
                data = json.loads(content)
            else:
                return "ok"
            
            # Extract 'data' field (contains markdown in nisaba standard format)
            data_field = data.get('data', '')
            
            # Parse markdown for **message**: field
            if isinstance(data_field, str):
                match = re.search(r'\*\*message\*\*:\s*\n\s*(.+)', data_field)
                if match:
                    return match.group(1).strip()
            
            # Fallback: use 'message' from root (also nisaba standard)
            if 'message' in data:
                return data['message']
                
        except Exception:
            pass
        
        return "ok"

    def _estimate_tokens(self, text: str) -> int:
        """
        Accurate token estimate using tiktoken.

        Args:
            text: Text to estimate

        Returns:
            Exact token count using cl100k_base encoding
        """
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception as e:
            logger.warning(f"tiktoken encoding failed, using fallback: {e}")
            # Fallback to rough estimate if tiktoken fails
            return len(text) // 4

    def _get_claude_project_dir_name(self, cwd: str) -> str:
        """
        Convert project path to Claude CLI normalized directory name.

        Claude Code normalization (web-confirmed + empirically verified):
        - Slashes and special characters (including underscores) → hyphens
        - Prepend with hyphen

        Examples:
            /home/y3i12/nabu_nisaba → -home-y3i12-nabu-nisaba
            /code/project-name → -code-project-name
        """
        real_path = Path(cwd).resolve()
        parts = list(real_path.parts)

        # Unix: remove leading /
        if parts[0] == "/":
            parts = parts[1:]

        # Join with - and normalize special chars to -
        normalized = "-".join(parts).replace("_", "-")

        return f"-{normalized}"

    def _get_session_jsonl_path(self) -> Optional[Path]:
        """Get path to current session's Claude Code transcript."""
        if not self.current_session_id:
            return None

        cwd = os.getcwd()
        project_dir_name = self._get_claude_project_dir_name(cwd)
        jsonl_path = Path.home() / ".claude" / "projects" / project_dir_name / f"{self.current_session_id}.jsonl"

        if not jsonl_path.exists():
            logger.debug(f"Session transcript not found: {jsonl_path}")
            return None

        return jsonl_path

    def _count_session_tokens_from_transcript(self) -> tuple[int, int]:
        """
        Load session transcript and count tokens using claude-code-log.

        Returns:
            Tuple of (input_tokens, output_tokens)
        """
        jsonl_path = self._get_session_jsonl_path()
        if not jsonl_path:
            return 0, 0

        try:
            from claude_code_log.parser import load_transcript
            from claude_code_log.models import AssistantTranscriptEntry

            entries = load_transcript(jsonl_path, silent=True)

            total_input = sum(
                e.message.usage.input_tokens
                for e in entries
                if isinstance(e, AssistantTranscriptEntry)
                and e.message.usage
            )
            total_output = sum(
                e.message.usage.output_tokens
                for e in entries
                if isinstance(e, AssistantTranscriptEntry)
                and e.message.usage
            )

            return total_input, total_output

        except Exception as e:
            logger.error(f"Failed to parse transcript: {e}")
            return 0, 0
    
    def _generate_status_bar(self, body: dict, workspace: 'WorkspaceFiles', tool_results:str = "") -> str:
        """
        Generate status bar with segmented token usage.

        Calculates workspace, messages, and total token counts.
        Shows window counts for context awareness.
        Exports JSON for external status line tools.

        Args:
            body: Request body dict
            workspace: Session-specific WorkspaceFiles instance

        Returns:
            Formatted status bar with tags
        """
        # Extract model name
        model_name = body.get('model', 'unknown')

        # Load all caches (triggers mtime-based updates)
        self._shared_system_prompt.load()
        workspace.todos.load()
        # workspace.notifications.load()
        workspace.augments.load()
        # workspace.structural_view.load()
        self._shared_transcript.load()
        workspace.core_system_prompt.load()

        # Use cached token counts (no recalculation!)
        # structural_view_tokens = workspace.structural_view.token_count
        # workspace_tokens = workspace.todos.token_count + workspace.notifications.token_count + structural_view_tokens

        # Count message tokens from session transcript (using claude-code-log)
        messages_input_tokens, messages_output_tokens = self._count_session_tokens_from_transcript()
        messages_tokens = messages_input_tokens + messages_output_tokens

        tool_tokens = self._estimate_tokens(json.dumps(body.get('tools', [])))
        # tool_result_tokens = self._estimate_tokens(tool_results)
        # Total usage
        # total_tokens = workspace_tokens + self._shared_system_prompt.token_count + workspace.core_system_prompt.token_count + tool_tokens + workspace.augments.token_count + tool_result_tokens + messages_tokens

        # Export JSON for external status line tools
        status_data = {
            "model": model_name,
            "workspace": {
#                "total": workspace_tokens,
                "system": {
                    "prompt": self._shared_system_prompt.token_count + workspace.core_system_prompt.token_count,
                    "tools": tool_tokens,
                    "transcript": self._shared_transcript.token_count
                },
                "augments": workspace.augments.token_count,
#                "view": structural_view_tokens,
#                "tool_results": tool_result_tokens,
#                "notifications": workspace.notifications.line_count - 1,  # header + endl compensation,
#                "todos": workspace.todos.line_count - 1  # header + endl compensation
            },
            "messages": messages_tokens,
            "total": 0,
            "budget": 200 * 1000
        }

        ws = status_data['workspace']
        status_data['total'] = ws['system']['prompt'] + ws['system']['tools'] + ws['system']['transcript'] + ws['augments'] + status_data['messages']
        
        parts = [
            f"MODEL({model_name})",
            f"{status_data['total']//1000}k/{status_data['budget']//1000}k",
            f"SYSTEM({ws['system']['prompt']//1000}k)",
            f"TOOLS({ws['system']['tools']//1000}k)",
            f"AUG({ws['augments']//1000}k)",
            f"COMPTRANS({ws['system']['transcript']//1000}k)",
            f"MSG({status_data['messages']//1000}k)",
        ]

        status = "\n".join([
            ' | '.join(parts[0:2]),
            ' | '.join(parts[2:])
        ])

        try:
            # Write to session-specific status bar file
            status_file = workspace.core_system_prompt.file_path.parent / "status_bar_live.txt"
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(status)
        except Exception as e:
            logger.warning(f"Failed to write status file: {e}")

        return f"---STATUS_BAR\n{status}\n---STATUS_BAR_END"

def load(loader):
    """
    Called when mitmproxy loads this script.

    This is the mitmproxy addon interface for script-based addons.
    """
    # Augments file comes from environment variable
    addon = AugmentInjector()

    # Add option for documentation
    loader.add_option(
        name="nisaba_augments_file",
        typespec=str,
        default="./.nisaba/tui/augment_view.md",
        help="Path to augments content file",
    )

    # Register the addon
    return addon


# Instantiate addon for mitmproxy
addons = [AugmentInjector()]
