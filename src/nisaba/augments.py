"""
Augments management system for dynamic context loading.

Provides functionality to load/unload augment files (markdown-based knowledge units)
and compose them for injection into Claude Code's system prompt.
"""

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from nisaba.structured_file import JsonStructuredFile
from nisaba.workspace_files import WorkspaceFiles

logger = logging.getLogger(__name__)


@dataclass
class Augment:
    """
    Represents a parsed augment.

    Attributes:
        group: Augment group/category (e.g., "dead_code_detection")
        name: Augment name (e.g., "find_unreferenced_callables")
        path: Full path identifier "group/name"
        content: The augment content (description, examples, queries, etc.)
        tools: List of tool names mentioned in TOOLS section
        requires: List of dependency augment paths (group/name format)
        file_path: Source file path for this augment
    """
    group: str
    name: str
    path: str
    content: str
    tools: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    file_path: Optional[Path] = None

    @property
    def display_name(self) -> str:
        """Get display name for this augment."""
        return f"{self.group}/{self.name}"

# Module-level session-keyed singleton
_AUGMENT_MANAGER_INSTANCES: Dict[str, 'AugmentManager'] = {}

def get_augment_manager(session_id: str) -> 'AugmentManager':
    """Get/create AugmentManager instance for session.

    Args:
        session_id: Session identifier (required)

    Returns:
        AugmentManager instance for this session

    Raises:
        ValueError: If session_id is empty or None
    """
    if not session_id:
        raise ValueError("session_id is required for AugmentManager")

    if session_id not in _AUGMENT_MANAGER_INSTANCES:
        augments_dir = Path.cwd() / ".nisaba" / "augments"
        _AUGMENT_MANAGER_INSTANCES[session_id] = AugmentManager(
            augments_dir=augments_dir,
            session_id=session_id
        )
    return _AUGMENT_MANAGER_INSTANCES[session_id]

class AugmentManager:
    """
    Manages augments lifecycle: loading, activation, composition.

    Augments are markdown files stored in a directory structure:
    {augments_dir}/{group_name}/{augment_name}.md

    Active augments are composed into a single markdown file that gets
    injected into Claude's context via the proxy.
    """

    def __init__(self, augments_dir: Path, session_id: str):
        """
        Initialize augments manager.

        Args:
            augments_dir: Directory containing augment files
            session_id: Session identifier (required)

        Raises:
            ValueError: If session_id is empty or None
        """
        if not session_id:
            raise ValueError("session_id is required for AugmentManager")

        self.augments_dir = Path(augments_dir)
        self.session_id = session_id

        # Get session-specific WorkspaceFiles instance
        workspace = WorkspaceFiles.instance(session_id)
        self.composed_file = workspace.augments.file_path

        # All available augments (loaded from disk)
        self.available_augments: Dict[str, Augment] = {}

        # Currently active augments
        self.active_augments: Set[str] = set()

        # Pinned augments (always active, cannot be deactivated)
        self.pinned_augments: Set[str] = set()

        # Tool association map (for guidance integration)
        # Maps tool_name -> [augment_paths that mention it]
        self._tool_associations: Dict[str, List[str]] = {}

        # Cached augment tree (for system prompt injection)

        # Load available augments from disk
        self._load_augments_from_dir()

        # Use JsonStructuredFile for atomic state persistence
        # Session-specific: active augments only
        self._state_file = JsonStructuredFile(
            file_path=self.state_file,
            name="augment_state",
            default_factory=lambda: {
                "active_augments": []
            }
        )

        # Global: pinned augments (shared across all sessions)
        self._pinned_file = JsonStructuredFile(
            file_path=Path(".nisaba/base_files/pinned_augments.json"),
            name="pinned_augments",
            default_factory=lambda: {
                "pinned_augments": []
            }
        )

        self.load_state()

    @property
    def state_file(self) -> Path:
        """Path to state persistence file (session-specific)."""
        return Path(f".nisaba/sessions/{self.session_id}/state.json")

    def save_state(self) -> None:
        """Save active augments to session-specific JSON file."""
        state = {
            "active_augments": sorted(self.active_augments)
        }

        # Use JsonStructuredFile for atomic write with locking
        self._state_file.write_json(state)
        logger.debug(f"Saved {len(self.active_augments)} active augments to session state file")

    def _save_pinned(self) -> None:
        """Save pinned augments to global JSON file (shared across sessions)."""
        state = {
            "pinned_augments": sorted(self.pinned_augments)
        }

        # Use JsonStructuredFile for atomic write with locking
        self._pinned_file.write_json(state)
        logger.debug(f"Saved {len(self.pinned_augments)} pinned augments to global file")

    def load_state(self) -> None:
        """Restore pinned (global) and active (session) augments from JSON files."""
        # 1. Load global pinned augments first (shared across sessions)
        pinned_state = self._pinned_file.load_json()
        pinned = pinned_state.get("pinned_augments", [])
        for aug_path in pinned:
            if aug_path in self.available_augments:
                self.pinned_augments.add(aug_path)
            else:
                logger.warning(f"Skipping unavailable pinned augment: {aug_path}")

        # 2. Load session-specific active augments
        session_state = self._state_file.load_json()
        active = session_state.get("active_augments", [])
        for aug_path in active:
            if aug_path in self.available_augments:
                self.active_augments.add(aug_path)
            else:
                logger.warning(f"Skipping unavailable augment: {aug_path}")

        # 3. Auto-activate pinned augments (merge into active set)
        self.active_augments.update(self.pinned_augments)

        # Rebuild tool associations and compose
        if self.active_augments:
            self._rebuild_tool_associations()
            self._compose_and_write()

        logger.info(f"Restored {len(self.active_augments)} active augments ({len(self.pinned_augments)} pinned) from state files")

    def _load_augments_from_dir(self) -> None:
        """Load all augment files from augments directory."""
        if not self.augments_dir.exists():
            logger.warning(f"Augments directory does not exist: {self.augments_dir}")
            return

        # Find all .md files in augments_dir
        for augment_file in self.augments_dir.rglob("*.md"):
            try:
                augment = self._parse_augment_file(augment_file)
                self.available_augments[augment.path] = augment
                logger.debug(f"Loaded augment: {augment.path}")
            except Exception as e:
                logger.warning(f"Failed to parse augment file {augment_file}: {e}")

        # Update augment tree cache after loading
        self._update_augment_tree_cache()

    def _parse_augment_file(self, file_path: Path) -> Augment:
        """
        Parse an augment markdown file.

        Expected format:
        # {group_name}
        ## {augment_name}
        Path: {group}/{name}

        {content}

        ## TOOLS
        - tool1()
        - tool2()

        ## REQUIRES
        - group/augment1
        - group/augment2

        Args:
            file_path: Path to augment file

        Returns:
            Parsed Augment object
        """
        content = file_path.read_text(encoding='utf-8')

        # Extract group and name from path
        # e.g., augments/dead_code_detection/find_unreferenced.md
        relative_path = file_path.relative_to(self.augments_dir)
        parts = relative_path.parts

        if len(parts) < 2:
            raise ValueError(f"Invalid augment file structure: {relative_path}")

        group = parts[0]
        name = parts[-1].replace('.md', '')
        path = f"{group}/{name}"

        # Extract TOOLS section
        tools = []
        tools_match = re.search(r'## TOOLS\s*\n((?:- .+\n?)+)', content, re.MULTILINE)
        if tools_match:
            tools_text = tools_match.group(1)
            # Extract tool names (remove - and () if present)
            tools = [
                re.sub(r'\(\)', '', line.strip('- \n'))
                for line in tools_text.split('\n')
                if line.strip().startswith('-')
            ]

        # Extract REQUIRES section
        requires = []
        requires_match = re.search(r'## REQUIRES\s*\n((?:- .+\n?)+)', content, re.MULTILINE)
        if requires_match:
            requires_text = requires_match.group(1)
            requires = [
                line.strip('- \n')
                for line in requires_text.split('\n')
                if line.strip().startswith('-')
            ]

        # Extract main content (everything before TOOLS section)
        if tools_match:
            main_content = content[:tools_match.start()].strip()
        else:
            main_content = content.strip()

        return Augment(
            group=group,
            name=name,
            path=path,
            content=main_content,
            tools=tools,
            requires=requires,
            file_path=file_path
        )

    def show_augments(self) -> Dict[str, List[str]]:
        """
        List all available augments grouped by category.

        Returns:
            Dict mapping group_name -> [augment_names]
        """
        grouped: Dict[str, List[str]] = {}

        for augment_path, augment in self.available_augments.items():
            if augment.group not in grouped:
                grouped[augment.group] = []
            grouped[augment.group].append(augment.name)

        return grouped

    def _generate_augment_tree(self) -> str:
        """
        Generate tree representation of ALL available augments.

        Pinned augments are marked with 📌 indicator.

        Returns:
            Formatted string showing augment hierarchy
        """
        augments_dict = self.show_augments()

        if not augments_dict:
            return "# available augments: (none)"

        lines = ["# available augments"]
        for group in sorted(augments_dict.keys()):
            lines.append(f"  {group}/")
            for augment_name in sorted(augments_dict[group]):
                augment_path = f"{group}/{augment_name}"
                pin_indicator = " 📌" if augment_path in self.pinned_augments else ""
                lines.append(f"    - {augment_name}{pin_indicator}")

        return "\n".join(lines)

    def _update_augment_tree_cache(self) -> None:
        """Update cached augment tree representation."""
        self._cached_augment_tree = self._generate_augment_tree()
        logger.debug(f"Updated augment tree cache: {len(self.available_augments)} augments")

    def activate_augments(
        self,
        patterns: List[str],
        exclude: List[str] = []
    ) -> Dict[str, List[str]]:
        """
        Activate augments matching patterns.

        Supports wildcards:
        - "group/*" - all augments in group
        - "group/augment_name" - specific augment
        - "*" or "**/*" - all augments

        Args:
            patterns: List of patterns to match
            exclude: List of patterns to exclude

        Returns:
            Dict with 'affected', 'dependencies'
        """
        self._load_augments_from_dir()

        to_activate: Set[str] = set()

        # Match patterns
        for pattern in patterns:
            matched = self._match_pattern(pattern)
            to_activate.update(matched)

        # Remove excluded
        for exclude_pattern in exclude:
            excluded = self._match_pattern(exclude_pattern)
            to_activate -= excluded

        # Resolve dependencies
        with_deps = self._resolve_dependencies(list(to_activate))

        # Separate direct loads from dependencies
        dependencies = set(with_deps) - to_activate

        # Update active augments
        self.active_augments.update(with_deps)

        # Update tool associations
        self._rebuild_tool_associations()

        # Compose and write
        self._compose_and_write()
        
        # Save state
        self.save_state()

        return {
            'affected': sorted(to_activate),
            'dependencies': sorted(dependencies)
        }

    def deactivate_augments(self, patterns: List[str]) -> Dict[str, List[str]]:
        """
        Deactivate augments matching patterns.

        Pinned augments cannot be deactivated and are silently skipped.

        Args:
            patterns: List of patterns to match

        Returns:
            Dict with 'unloaded' and 'skipped' lists
        """
        to_deactivate: Set[str] = set()

        for pattern in patterns:
            matched = self._match_pattern(pattern)
            # Only deactivate if currently active
            to_deactivate.update(matched & self.active_augments)

        # Separate pinned from deactivatable
        pinned_skipped = to_deactivate & self.pinned_augments
        to_deactivate -= self.pinned_augments

        # Remove from active
        self.active_augments -= to_deactivate

        # Rebuild tool associations
        self._rebuild_tool_associations()

        # Compose and write
        self._compose_and_write()

        # Save state
        self.save_state()

        return {
            'affected': sorted(to_deactivate),
            'skipped': sorted(pinned_skipped)
        }

    def pin_augment(self, patterns: List[str]) -> Dict[str, List[str]]:
        """
        Pin augments matching patterns (always active, cannot be deactivated).

        Args:
            patterns: List of patterns to match

        Returns:
            Dict with 'affected' list
        """
        to_pin: Set[str] = set()

        for pattern in patterns:
            matched = self._match_pattern(pattern)
            to_pin.update(matched)

        # Add to pinned set
        self.pinned_augments.update(to_pin)

        # Ensure pinned augments are active
        self.active_augments.update(to_pin)

        # Rebuild tool associations
        self._rebuild_tool_associations()

        # Compose and write
        self._compose_and_write()

        # Save state: active to session, pinned to global
        self.save_state()
        self._save_pinned()

        # Update augment tree cache (to show pin indicators)
        self._update_augment_tree_cache()

        return {
            'affected': sorted(to_pin)
        }

    def unpin_augment(self, patterns: List[str]) -> Dict[str, List[str]]:
        """
        Unpin augments matching patterns (allows deactivation).

        Note: Does not deactivate the augments, just removes pin protection.

        Args:
            patterns: List of patterns to match

        Returns:
            Dict with 'affected' list
        """
        to_unpin: Set[str] = set()

        for pattern in patterns:
            matched = self._match_pattern(pattern)
            # Only unpin if currently pinned
            to_unpin.update(matched & self.pinned_augments)

        # Remove from pinned set
        self.pinned_augments -= to_unpin

        # Save pinned to global file
        self._save_pinned()

        # Update augment tree cache (to remove pin indicators)
        self._update_augment_tree_cache()

        return {
            'affected': sorted(to_unpin)
        }

    def learn_augment(self, group: str, name: str, content: str) -> Dict[str, List[str]]:
        """
        Create a new augment.

        Args:
            group: Augment group
            name: Augment name
            content: Augment content (markdown)

        Returns:
            affected
        """
        # Create group directory if needed
        group_dir = self.augments_dir / group
        group_dir.mkdir(parents=True, exist_ok=True)

        # Write augment file
        augment_file = group_dir / f"{name}.md"
        augment_file.write_text(content, encoding='utf-8')

        # Parse and add to available augments
        augment = self._parse_augment_file(augment_file)
        self.available_augments[augment.path] = augment

        # Update augment tree cache after adding new augment
        self._update_augment_tree_cache()

        logger.info(f"Created augment: {augment.path}")

        return {
            'affected': [ augment.path ]
        }

    def _match_pattern(self, pattern: str) -> Set[str]:
        """
        Match augment paths against a pattern.

        Args:
            pattern: Pattern to match (supports * wildcard)

        Returns:
            Set of matching augment paths
        """
        matched = set()

        # Convert glob pattern to regex
        if pattern == "*" or pattern == "**/*":
            # Match all
            return set(self.available_augments.keys())

        # Replace * with regex pattern
        regex_pattern = pattern.replace('*', '.*')
        regex_pattern = f'^{regex_pattern}$'

        try:
            compiled = re.compile(regex_pattern)
            for augment_path in self.available_augments.keys():
                if compiled.match(augment_path):
                    matched.add(augment_path)
        except re.error as e:
            logger.warning(f"Invalid pattern '{pattern}': {e}")

        return matched

    def _resolve_dependencies(self, augment_paths: List[str]) -> List[str]:
        """
        Resolve dependencies for given augments.

        Uses BFS to find all required augments, with cycle detection.

        Args:
            augment_paths: List of augment paths to resolve

        Returns:
            List of augment paths including dependencies
        """
        resolved = set(augment_paths)
        to_process = list(augment_paths)
        processed = set()

        while to_process:
            current_path = to_process.pop(0)

            if current_path in processed:
                continue

            processed.add(current_path)

            augment = self.available_augments.get(current_path)
            if not augment:
                logger.warning(f"Augment not found: {current_path}")
                continue

            # Add dependencies
            for dep_path in augment.requires:
                if dep_path not in resolved:
                    resolved.add(dep_path)
                    to_process.append(dep_path)

        return sorted(resolved)

    def _rebuild_tool_associations(self) -> None:
        """Rebuild tool association map from active augments."""
        self._tool_associations.clear()

        for augment_path in self.active_augments:
            augment = self.available_augments.get(augment_path)
            if not augment:
                continue

            # For each tool mentioned in this augment
            for tool_name in augment.tools:
                if tool_name not in self._tool_associations:
                    self._tool_associations[tool_name] = []

                # Add other tools from this augment as related
                for other_tool in augment.tools:
                    if other_tool != tool_name and other_tool not in self._tool_associations[tool_name]:
                        self._tool_associations[tool_name].append(other_tool)

    def _compose_and_write(self) -> None:
        """Compose active augments into single markdown file."""
        # Start with augment tree (always present)
        parts = []
        if self._cached_augment_tree:
            parts.append(self._cached_augment_tree)

        if not self.active_augments:
            # Only augment tree, no active augments
            content = parts[0] if parts else ""
            # Write via session-specific workspace (updates cache + file atomically)
            WorkspaceFiles.instance(self.session_id).augments.write(content)
            logger.info(f"No active augments - wrote augment tree only for session {self.session_id}")
            return

        # Group augments by group
        grouped: Dict[str, List[Augment]] = {}
        for augment_path in sorted(self.active_augments):
            augment = self.available_augments.get(augment_path)
            if not augment:
                continue

            if augment.group not in grouped:
                grouped[augment.group] = []
            grouped[augment.group].append(augment)

        # Compose active augments markdown
        lines = []

        for group_name in sorted(grouped.keys()):
            lines.append(f"# {group_name.replace('_', ' ').title()}")
            lines.append("")

            for augment in sorted(grouped[group_name], key=lambda s: s.name):
                lines.append(f"## {augment.name.replace('_', ' ').title()}")
                lines.append(f"Path: {augment.path}")
                lines.append("")
                lines.append(augment.content)
                lines.append("")
                lines.append("---")
                lines.append("")

        parts.append("\n".join(lines))
        content = "\n\n".join(parts)

        # Write via session-specific workspace (updates cache + file atomically)
        WorkspaceFiles.instance(self.session_id).augments.write(content)

        logger.info(f"Composed {len(self.active_augments)} augments for session {self.session_id}")
