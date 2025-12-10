# WorkspaceFiles Singleton Architecture

**Purpose:** Centralized registry for shared workspace files with guaranteed synchronization across all components.

---

## The Problem

**Before WorkspaceFiles:**

Multiple components created independent `StructuredFileCache` instances for the same files:

```python
# proxy.py
self.todos_cache = StructuredFileCache(Path(".nisaba/tui/todo_view.md"), ...)

# todo.py (module-level)
TODO_FILE_CACHE = StructuredFileCache(Path(".nisaba/tui/todo_view.md"), ...)

# Risk: Two cache instances = potential inconsistency
```

**Issues:**
1. **Duplicate caches** - Same file, different cache instances
2. **Sync risk** - Component A writes, Component B has stale cached state
3. **Scattered paths** - File paths duplicated across codebase
4. **No inventory** - Unclear what workspace files exist
5. **Testing complexity** - Can't easily inject mock caches

---

## The Solution: WorkspaceFiles Singleton

**Core principle:** Single source of truth for all workspace file caches.

```python
# src/nisaba/workspace_files.py

class WorkspaceFiles:
    """Singleton registry for shared workspace files."""
    
    _instance: Optional['WorkspaceFiles'] = None
    
    def __init__(self):
        # Workspace markdown files (system prompt sections)
        self.augments = StructuredFileCache(...)
        self.system_prompt = StructuredFileCache(...)
        self.core_system_prompt = StructuredFileCache(...)
        self.structural_view = StructuredFileCache(...)
        self.todos = StructuredFileCache(...)
        self.notifications = StructuredFileCache(...)
        self.transcript = StructuredFileCache(...)
        
        # Shared JSON state
        self.notification_state = JsonStructuredFile(...)
        self.mcp_servers = JsonStructuredFile(...)
    
    @classmethod
    def instance(cls) -> 'WorkspaceFiles':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
```

---

## Architecture Model

```
┌─────────────────────────────────────────────────────────┐
│                  WorkspaceFiles Singleton                │
│  (Single instance, created on first .instance() call)   │
├─────────────────────────────────────────────────────────┤
│  Workspace Sections (StructuredFileCache):              │
│    - augments          (.nisaba/tui/augment_view.md)    │
│    - system_prompt     (.nisaba/tui/system_prompt.md)   │
│    - core_system_prompt (.nisaba/tui/core_system_...)   │
│    - structural_view   (.nisaba/tui/structural_view.md) │
│    - todos             (.nisaba/tui/todo_view.md)       │
│    - notifications     (.nisaba/tui/notification_...)   │
│    - transcript        (.nisaba/tui/compacted_trans...) │
│                                                          │
│  Shared State (JsonStructuredFile):                     │
│    - notification_state (.nisaba/tui/notification_...)  │
│    - mcp_servers       (.nisaba/mcp_servers.json)       │
└─────────────────────────────────────────────────────────┘
         ▲              ▲               ▲              ▲
         │              │               │              │
    ┌────┴───┐   ┌──────┴──────┐  ┌────┴────┐  ┌─────┴─────┐
    │ Proxy  │   │  TodoTool   │  │ StructV │  │ precompact│
    │        │   │             │  │  Tool   │  │  _extract │
    └────────┘   └─────────────┘  └─────────┘  └───────────┘
    
All components access the SAME cache instances via .instance()
```

---

## What's Included vs Excluded

### ✓ Managed by WorkspaceFiles (shared, multi-component)

**Workspace sections** (visible in system prompt):
- `augment_view.md` - Written by: AugmentManager, Read by: Proxy
- `system_prompt.md` - Written by: External/manual, Read by: Proxy
- `core_system_prompt.md` - Written by: Proxy, Read by: Proxy
- `structural_view.md` - Written by: StructuralViewTool, Read by: Proxy
- `todo_view.md` - Written by: TodoTool, Read by: Proxy
- `notification_view.md` - Written by: Proxy, Read by: Proxy
- `compacted_transcript.md` - Written by: precompact_extract.py, Read by: Proxy

**Shared state** (cross-component coordination):
- `notification_state.json` - Proxy checkpoint tracking
- `mcp_servers.json` - Server discovery registry

### ✗ NOT Managed (component-private state)

These stay as component-local `JsonStructuredFile` instances:
- `augment_state.json` - AugmentManager internal state
- `structural_view_state.json` - StructuralViewTUI internal state
- `file_window_state.json` - FileWindowsManager internal state
- `request_modifier_state.json` - RequestModifier internal state

**Rationale:** Private state files are owned by single components, no cross-component access needed.

---

## Component Integration

### Pattern: Import and use .instance()

All components follow the same pattern:

```python
from nisaba.workspace_files import WorkspaceFiles

# Get singleton
files = WorkspaceFiles.instance()

# Access specific cache
files.todos.write(content)
content = files.notifications.load()
files.structural_view.write(markdown)
```

### Proxy (src/nisaba/wrapper/proxy.py)

```python
class AugmentInjector:
    def __init__(self):
        # Get shared workspace files singleton
        self.files = WorkspaceFiles.instance()
        
        # Aliases for backward compatibility
        self.augments_cache = self.files.augments
        self.todos_cache = self.files.todos
        # ... etc
```

**Uses:** All workspace caches for system prompt injection

### TodoTool (src/nisaba/tools/todo.py)

```python
class TodoTool:
    @classmethod
    def get_todo_file_cache(cls):
        return WorkspaceFiles.instance().todos
    
    @classmethod
    def add(cls, todos: List[str], position: int = None):
        cls.get_todo_file_cache().atomic_update(...)
```

**Uses:** `todos` cache for workspace todo operations

### StructuralViewTool (src/nabu/mcp/tools/structural_view_tool.py)

```python
class StructuralViewTool:
    def _write_state(self, tree_markdown: str):
        WorkspaceFiles.instance().structural_view.write(tree_markdown)
```

**Uses:** `structural_view` cache for tree rendering

### precompact_extract.py (scripts/precompact_extract.py)

```python
def main():
    cache = WorkspaceFiles.instance().transcript
    existing = cache.content if cache.content else ""
    cache.write(existing + "\n\n" + new_transcript)
```

**Uses:** `transcript` cache for compaction hook

### MCPServerRegistry (src/nisaba/mcp_registry.py)

```python
class MCPServerRegistry:
    def __init__(self, registry_path: Optional[Path] = None):
        # Use shared singleton instead of creating own instance
        self._file = WorkspaceFiles.instance().mcp_servers
        self.registry_path = self._file.file_path

        # Validate registry_path if provided (backward compatibility)
        if registry_path is not None and Path(registry_path) != self.registry_path:
            logger.warning(
                f"Custom registry_path {registry_path} ignored, "
                f"using WorkspaceFiles singleton: {self.registry_path}"
            )
```

**Uses:** `mcp_servers` cache for server discovery registry

### AugmentManager (src/nisaba/augments.py)

```python
class AugmentManager:
    def __init__(self, augments_dir: Path, composed_file: Path):
        # Verify composed_file matches WorkspaceFiles singleton
        expected_path = WorkspaceFiles.instance().augments.file_path
        if self.composed_file.resolve() != expected_path.resolve():
            logger.warning(
                f"AugmentManager composed_file {self.composed_file} differs from "
                f"WorkspaceFiles.augments {expected_path}. Using singleton path."
            )
            self.composed_file = expected_path

    def _compose_and_write(self) -> None:
        """Compose active augments and write via WorkspaceFiles singleton."""
        # ... composition logic ...
        WorkspaceFiles.instance().augments.write(content)
```

**Uses:** `augments` cache for composed augments output

---

## Synchronization Guarantees

**StructuredFileCache provides:**
1. **mtime-based caching** - Re-reads only if file externally modified
2. **Write-updates-cache** - No re-read after write (zero latency)
3. **Thread safety** - `threading.Lock()` for in-process coordination
4. **Process safety** - `fcntl.flock()` for inter-process coordination
5. **Atomic transactions** - `atomic_update()` for read-modify-write

**WorkspaceFiles adds:**
6. **Single instance guarantee** - Impossible to have duplicate caches
7. **Consistent state** - All components see same cache instance
8. **Centralized inventory** - All workspace files defined in one place

---

## Benefits

### 1. Guaranteed Synchronization
```python
# Component A writes
WorkspaceFiles.instance().todos.write("new content")

# Component B reads (same cache instance!)
content = WorkspaceFiles.instance().todos.load()
# ✓ Sees new content immediately (no mtime check needed)
```

### 2. Clear Inventory
```python
# See all workspace files at a glance
files = WorkspaceFiles.instance()
print(files.augments.file_path)        # .nisaba/tui/augment_view.md
print(files.todos.file_path)           # .nisaba/tui/todo_view.md
print(files.structural_view.file_path) # .nisaba/tui/structural_view.md
```

### 3. Type Safety
IDE autocomplete shows all available caches:
```python
files = WorkspaceFiles.instance()
files.  # <-- IDE suggests: augments, todos, structural_view, etc.
```

### 4. Testing Support
```python
# Inject mock for testing
mock_files = MockWorkspaceFiles()
WorkspaceFiles._instance = mock_files

# All components now use mock
assert TodoTool.get_todo_file_cache() is mock_files.todos
```

### 5. Debugging Visibility
```python
# Inspect all workspace state in one place
files = WorkspaceFiles.instance()
print(f"Todos content: {files.todos.content}")
print(f"Todos tokens: {files.todos.token_count}")
print(f"Structural view lines: {files.structural_view.line_count}")
```

---

## Migration Notes

**Before refactoring:**
- Proxy created 7 StructuredFileCache instances
- TodoTool had module-level singleton `TODO_FILE_CACHE`
- Each component duplicated file paths
- No guarantee of cache instance consistency

**After refactoring:**
- All components use `WorkspaceFiles.instance()`
- Single cache instance per file (guaranteed)
- File paths defined once in `workspace_files.py`
- Impossible to have inconsistent state

**Backward compatibility:**
- Proxy uses aliases (`self.todos_cache = self.files.todos`)
- Existing code continues to work unchanged
- Can remove aliases after verification

**Latest refactoring (Complete WorkspaceFiles Adoption):**
- MCPServerRegistry refactored to use singleton (eliminated duplicate cache instances)
- AugmentManager dual write paths fixed (now writes via singleton)
- Proxy notification write fixed (now uses singleton)
- FileCache class removed (dead code, 40 lines deleted)
- Zero cache duplication vulnerabilities remain

---

## Usage Guidelines

### When to add a file to WorkspaceFiles

Add to singleton if:
- ✓ Written by one component, read by another
- ✓ Visible in Claude's workspace/system prompt
- ✓ Multiple components need access
- ✓ Needs synchronization guarantees

Keep private if:
- ✓ Single component owns the file
- ✓ Internal state persistence only
- ✓ No cross-component coordination needed

### Example: Adding a new workspace section

```python
# 1. Add to WorkspaceFiles.__init__
self.new_section = StructuredFileCache(
    file_path=Path(".nisaba/tui/new_section.md"),
    name="new section",
    tag="NEW_SECTION"
)

# 2. Use in components
from nisaba.workspace_files import WorkspaceFiles

files = WorkspaceFiles.instance()
files.new_section.write(content)

# 3. Proxy automatically includes in injection
# (if tag matches injection pattern)
```

---

## Implementation Details

### File Locations

```
src/nisaba/
├── workspace_files.py          # Singleton definition
├── structured_file.py          # Base cache classes
├── wrapper/
│   └── proxy.py               # Uses singleton
└── tools/
    └── todo.py                 # Uses singleton

src/nabu/mcp/tools/
└── structural_view_tool.py     # Uses singleton

scripts/
└── precompact_extract.py       # Uses singleton
```

### Dependencies

```
WorkspaceFiles
    ├─ depends on: StructuredFileCache, JsonStructuredFile
    └─ used by: Proxy, TodoTool, StructuralViewTool, precompact_extract,
                MCPServerRegistry, AugmentManager

StructuredFileCache
    ├─ mtime-based caching
    ├─ write-updates-cache optimization
    ├─ threading.Lock() for thread safety
    ├─ fcntl.flock() for process safety
    └─ atomic_update() for transactions
```

---

## Verification After Restart

After proxy/CLI restart, verify:

```bash
# Check logs for singleton initialization
tail .nisaba/logs/proxy.log | grep -i workspace

# Verify single instance
python -c "
from nisaba.workspace_files import WorkspaceFiles
f1 = WorkspaceFiles.instance()
f2 = WorkspaceFiles.instance()
assert f1 is f2, 'Singleton violated!'
print('✓ Singleton verified')
"

# Test todo operation (uses shared cache)
# Via CLI: use todo tool, check proxy sees same instance

# Test structural view (uses shared cache)
# Expand node, verify proxy loads updated content
```

---

## Key Insights

**Architecture pattern:**
- Singleton provides **spatial consistency** (same instance everywhere)
- StructuredFileCache provides **temporal consistency** (mtime, atomic updates)
- Together: **complete consistency guarantee**

**Design decision:**
- Shared workspace files → singleton managed
- Private component state → component managed
- Clear separation of concerns

**Zero overhead:**
- Singleton adds no performance cost
- StructuredFileCache already optimized (mtime, write-updates-cache)
- Single instance reduces memory footprint

---

**Clean. Simple. Elegant. Sophisticated. Sharp. Sexy.** 🖤

---

**REQUIRES:**
- foundation/nisaba_infrastructure_flow (for understanding StructuredFileCache)
- __base/001_workspace_paradigm (for workspace mental model)

**RELATED:**
- dev_mode_architecture_reference/nisaba_tool_implementation
- dev_mode_architecture_reference/file_windows_tui_architecture
