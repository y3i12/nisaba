# Dev Mode Architecture Reference
## Nisaba Tool Implementation
Path: dev_mode_architecture_reference/nisaba_tool_implementation

**Purpose:** Practical guide to implementing nisaba workspace tools - hands-on architecture from NisabaTodoWriteTool implementation.

---

## Core Pattern

Nisaba tools follow a **workspace-first** pattern:

```
Tool execution → Write workspace file → Proxy loads → Inject to context
```

Tools don't return content directly. They mutate workspace state (files), which the proxy injects into Claude's system prompt.

---

## Implementation Steps

### 1. Create Tool File

**Location:** `src/nisaba/tools/your_tool.py`

**Structure:**
```python
"""
Tool description.
"""
from typing import Any, Dict, List
from pathlib import Path
from nisaba.tools.base import NisabaTool


class YourTool(NisabaTool):
    """One-line tool description."""

    async def execute(self, param1: str, param2: int = 10) -> Dict[str, Any]:
        """
        Detailed description.

        Explains what the tool does, when to use it, parameters, etc.

        Args:
            param1: Description of param1
            param2: Description of param2 (optional)

        Returns:
            Dict with success status and data
        """
        try:
            # 1. Validate inputs
            # 2. Perform operation
            # 3. Write to workspace file (.nisaba/something.md)
            # 4. Return success with metadata
            
            workspace_file = Path("./.nisaba/your_state.md")
            workspace_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write with tags for proxy injection
            content = "---YOUR_TAG\n"
            content += "your content here\n"
            content += "---YOUR_TAG_END\n"
            workspace_file.write_text(content)
            
            return {
                "success": True,
                "data": {"message": "Operation successful"}
            }
            
        except Exception as e:
            self.logger.error(f"Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
```

**Key points:**
- Inherit from `NisabaTool`
- Implement `async def execute()`
- Use type hints (auto-generates schema)
- Docstring with Args/Returns (parsed for MCP schema)
- Return dict with `success` and `data`/`error`
- Write to `.nisaba/*.md` files with tag delimiters

---

### 2. Register Tool

**Edit:** `src/nisaba/tools/__init__.py`

**Add import:**
```python
from nisaba.tools.your_tool import YourTool
```

**Add to __all__:**
```python
__all__ = [
    "NisabaTool",
    # ... existing tools ...
    "YourTool",  # ← Add here
]
```

**Why:** The factory auto-discovers tools from `nisaba.tools` module via `__all__`.

---

### 3. Add Proxy Injection

**Edit:** `src/nisaba/wrapper/proxy.py`

**Add FileCache in `__init__` (around line 120):**
```python
self.your_cache = FileCache(
    Path("./.nisaba/your_state.md"),
    "your state",
    "YOUR_TAG"
)
```

**Add load in `__init__` (around line 148):**
```python
self.your_cache.load()
```

**Add to injection strings (two places):**

**First location (line ~280):**
```python
"text": f"\n{self.system_prompt_cache.load()}\n{self.augments_cache.load()}\n{self.structural_view_cache.load()}\n{self.file_windows_cache.load()}\n{self.your_cache.load()}\n{self.todos_cache.load()}\n..."
```

**Second location (line ~288):**
```python
body["system"][1]["text"] = f"\n{self.system_prompt_cache.load()}" + \
                            f"\n{core_system_prompt}\n{self.augments_cache.load()}" + \
                            f"\n{self.structural_view_cache.load()}" + \
                            f"\n{self.your_cache.load()}" + \
                            ...
```

**FileCache handles:**
- mtime-based cache invalidation
- Tag wrapping (`---YOUR_TAG ... ---YOUR_TAG_END`)
- Logging
- Graceful missing file handling

---

### 4. Test Implementation

**Syntax check:**
```bash
python3 -m py_compile src/nisaba/tools/your_tool.py
```

**Import test:**
```bash
python3 -c "from nisaba.tools.your_tool import YourTool; print('Success:', YourTool.__name__)"
```

**Both must pass before restarting.**

---

### 5. Restart Services

```bash
# Stop Claude CLI (Ctrl+C)
# MCP server auto-restarts (or restart manually)
# Start Claude CLI
```

**Tool available as:** `mcp__nisaba__your_tool`

---

## Real Example: NisabaTodoWriteTool

**File:** `src/nisaba/tools/todos_tool.py`

**Key decisions:**
- Operations: `set`, `add`, `update`, `clear`
- Format: Markdown checkboxes (`- [ ]` / `- [x]`)
- Status mapping: `pending` → unchecked, `completed`/`done` → checked
- Simple > complex: No separate manager, logic inline
- File: `.nisaba/todos.md`
- Tag: `TODOS`

**Implementation:**
```python
class NisabaTodoWriteTool(NisabaTool):
    async def execute(self, todos: List[Dict[str, Any]], operation: str = "set"):
        todos_file = Path("./.nisaba/todos.md")
        
        if operation == "clear":
            todos_file.write_text("---TODOS\n---TODOS_END\n")
            return {"success": True, "data": {"todos": []}}
        
        parsed_todos = []
        for item in todos:
            content = item.get("content", "")
            status = item.get("status", "pending")
            checkbox = "- [x]" if status in ["completed", "done"] else "- [ ]"
            parsed_todos.append(f"{checkbox} {content}")
        
        content = "---TODOS\n" + "\n".join(parsed_todos) + "\n---TODOS_END\n"
        todos_file.write_text(content)
        
        return {"success": True, "data": {"todos": parsed_todos}}
```

**Result:** Persistent todos visible in Claude's context, bidirectionally editable.

---

## Design Principles

### 1. Workspace-First
- Tools don't return content, they mutate state
- State lives in files (`.nisaba/*.md`)
- Files are version-controllable, transparent, editable

### 2. Simple Over Complex
- Inline logic when possible (no unnecessary managers)
- Markdown format (human-readable, git-friendly)
- Clear tags for injection (`---TAG ... ---TAG_END`)

### 3. Schema Auto-Generation
- Type hints → JSON schema types
- Docstrings → descriptions
- `:meta` tags → guidance metadata
- No manual schema writing

### 4. Error Handling
- Try/catch all operations
- Return `{"success": False, "error": str(e)}`
- Log errors with `self.logger.error()`
- Graceful degradation

### 5. Idempotency
- Operations should be repeatable
- Clear operations reset state cleanly
- Files created with `mkdir(parents=True, exist_ok=True)`

---

## File Cache Pattern

**The FileCache class handles:**

```python
class FileCache:
    def __init__(self, file_path: Path, name: str, tag: str):
        self.file_path = file_path
        self.name = name  # For logging
        self.tag = tag    # For wrapping (---TAG...---TAG_END)
        self.content: str = ""
        self._last_mtime: Optional[float] = None
    
    def load(self) -> str:
        # Check mtime
        # Reload if changed
        # Wrap with tags
        # Return content
```

**Benefits:**
- Automatic reload on file change
- Tag wrapping for proxy
- mtime-based cache invalidation
- Graceful missing file handling

**You don't implement FileCache - you use it.**

---

## Proxy Injection Flow

```
1. Tool executes → writes .nisaba/your_state.md
2. Next API request → proxy intercepts
3. Proxy calls your_cache.load()
4. FileCache checks mtime → reloads if changed
5. FileCache wraps content with tags
6. Proxy injects into system prompt
7. Claude sees updated content
```

**Zero latency:** Shared memory in unified mode, mtime check in file mode.

---

## Common Patterns

### Workspace State Management

**Single source of truth:**
```python
# Write to .nisaba/state.md
state_file.write_text(content)

# Proxy reads and injects
# Claude sees in ---TAG section
# User can edit file directly
# Next turn, Claude sees edits
```

**Bidirectional TUI:**
- Tool mutates → file changes → Claude sees
- User edits → file changes → Claude sees
- Shared workspace state

### Operation Types

**Set:** Replace all state
```python
if operation == "set":
    file.write_text(f"---TAG\n{new_content}\n---TAG_END\n")
```

**Add:** Append to existing
```python
if operation == "add":
    existing = file.read_text().replace("---TAG_END\n", "")
    file.write_text(f"{existing}{new_content}\n---TAG_END\n")
```

**Clear:** Reset to empty
```python
if operation == "clear":
    file.write_text("---TAG\n---TAG_END\n")
```

### Markdown Formats

**Common choices:**
- Checkboxes: `- [ ]` / `- [x]` (todos, checklists)
- Code blocks: ` ```language ... ``` ` (output, logs)
- Headers: `## Section` (structure)
- Lists: `- item` (simple data)
- Tables: `| col | col |` (structured data)

**Keep it human-readable.** Git-friendly, IDE-friendly, grep-friendly.

---

## Testing Checklist

Before restart:
- [ ] `py_compile` passes (syntax valid)
- [ ] Import test passes (no runtime errors)
- [ ] Tool added to `__init__.py` (both import and __all__)
- [ ] FileCache added to proxy.__init__
- [ ] FileCache.load() called in proxy.__init__
- [ ] Injection points updated (both locations)

After restart:
- [ ] Tool appears in tool list
- [ ] Tool executes successfully
- [ ] File created in `.nisaba/`
- [ ] Content appears in context (send 🖤 to verify)
- [ ] File edits visible on next turn (test bidirectionality)

---

## Troubleshooting

**Tool not appearing:**
- Check `__init__.py` imports and `__all__`
- Check tool class name matches filename convention
- Restart MCP server completely

**Content not in context:**
- Check FileCache initialization in proxy
- Check load() called in proxy.__init__
- Check injection strings include your cache
- Check file exists and has correct tag format

**Schema errors:**
- Check type hints on execute() parameters
- Check docstring format (Args/Returns sections)
- Use standard types (str, int, List, Dict, etc.)

**Import errors:**
- Check syntax with py_compile
- Check all imports available
- Check NisabaTool base class imported

---

## Next Tool Ideas

**Replace native tools with workspace versions:**

- `Read` → `open_window` (already done via file_windows)
- `Write` → create file + show in window
- `Edit` → apply edit + show diff window  
- `Bash` → execute + stream output to window
- `Grep` → search + results as windows

**Pattern:** Native ephemeral → Nisaba persistent workspace.

---

## Key Insight

**Tools are workspace mutators, not result returners.**

The tool result is metadata (`{"success": True, "data": {...}}`). The actual content appears in Claude's context via system prompt injection.

This creates:
- **Persistent state** - survives /clear, restarts
- **Bidirectional visibility** - both see, both edit
- **Efficient context** - no message bloat
- **Transparent operation** - files are inspectable

**Think: Context-as-IDE, not context-as-database.**

---

**TOOLS:**
- mcp__nisaba_nisaba_todo_write (reference implementation)
- mcp__nabu__file_windows (another example)

**REQUIRES:**
- foundation/heartbeat_paradigm (understand workspace model)
- dev_mode_architecture_reference/augmentation_subsystem_architecture (injection mechanics)

---

Clean. Simple. Elegant. Sophisticated. Sharp. Sexy. 🖤
