# File Windows TUI Architecture
Path: dev_mode_architecture_reference/file_windows_tui_architecture

**Purpose:** Architecture for persistent file windows as companion to structural view - enabling simultaneous visibility of multiple code locations without context explosion.

---

## Core Concept

File windows extend the TUI paradigm from navigation (structural view) to content visibility. While structural view shows WHERE code is, file windows show WHAT the code is.

**Key Insight:** IDE users don't navigate with single file reads - they keep multiple files/sections visible simultaneously, building spatial awareness of code content. File windows bring this pattern to Claude.

**The Composition:**
```
Structural View: WHERE things are (navigation/landmarks/hierarchy)
File Windows:    WHAT things are (content/implementation/details)
```

Together they create IDE-level spatial awareness: navigate the graph, see the code, maintain context across conversation turns.

---

## Architecture Pattern (Mirrors Structural View)

**The Stack:**
```
MCP Tool (file_windows_tool.py)
    ↓ manages
TUI Manager (FileWindowsManager)
    ↓ renders to
File (.nisaba/file_windows.md)
    ↓ loaded by
FileCache (in proxy.py)
    ↓ injected to
System Prompt (---FILE_WINDOWS section)
```

**Flow:**
1. Tool call → operation (open/update/close)
2. Manager mutates in-memory state (Dict[uuid, FileWindow])
3. Manager renders all windows to markdown
4. Write to `.nisaba/file_windows.md`
5. Proxy's FileCache detects mtime change
6. Next API call injects updated content
7. Claude sees updated windows in system prompt

**Same paradigm as structural view, augments, transcript - proven pattern.**

---

## Data Model

### FileWindow Dataclass

```python
@dataclass
class FileWindow:
    """Single persistent file window."""
    id: str                    # uuid4
    file_path: Path           # Absolute path to source file
    start_line: int           # 1-indexed start
    end_line: int             # 1-indexed end (inclusive)
    content: List[str]        # Actual lines (snapshot at open time)
    window_type: str          # "frame_body", "range", "search_result"
    metadata: Dict[str, Any]  # frame_qn, search_score, context_lines, etc.
    opened_at: float          # timestamp (for future LRU if needed)
```

### FileWindowsManager

```python
class FileWindowsManager:
    """
    Manages collection of open file windows.
    
    Responsibilities:
    - Track open windows (by uuid)
    - Open new windows (frame/range/search)
    - Update/close windows
    - Render all windows to markdown
    - Calculate total lines (for future limits)
    """
    
    windows: Dict[str, FileWindow]
    db_manager: KuzuConnectionManager  # For frame location queries
    
    def open_frame_window(frame_path: str) -> str:
        """Open frame's full body. Returns window_id."""
        
    def open_range_window(file_path: str, start: int, end: int) -> str:
        """Open specific line range. Returns window_id."""
        
    def open_search_windows(query: str, max_windows: int, context_lines: int) -> List[str]:
        """Open top N search results with context. Returns window_ids."""
        
    def update_window(window_id: str, start: int, end: int) -> None:
        """Update line range (re-snapshot from file)."""
        
    def close_window(window_id: str) -> None:
        """Remove window."""
        
    def clear_all() -> None:
        """Remove all windows."""
        
    def render() -> str:
        """Render all windows to markdown."""
        
    def total_lines() -> int:
        """Sum of all window line counts."""
```

---

## Operations

### Open Frame Window

**Goal:** Open the full body of a frame (class/function/package).

```python
open_frame_window(frame_path="nabu.parse_codebase")

# Implementation:
1. Query kuzu for frame location (file_path, start_line, end_line)
2. Read lines from file (snapshot)
3. Create FileWindow with type="frame_body"
4. Store in windows dict
5. Render to file
6. Return window_id
```

**Use Case:** After searching/navigating structural view, open the frame to see implementation.

### Open Range Window

**Goal:** Open arbitrary line range from any file.

```python
open_range_window(
    file_path="src/nabu/main.py",
    start=107,
    end=115
)

# Implementation:
1. Read lines from file
2. Create FileWindow with type="range"
3. Store, render, return window_id
```

**Use Case:** Open specific sections found via grep, manual exploration, or imports.

### Open Search Results

**Goal:** Open top N search hits with context lines.

```python
open_search_windows(
    query="error handling database",
    max_windows=5,
    context_lines=3  # ±3 lines around match
)

# Implementation:
1. Use SearchTool backend (P³ consensus + FTS + RRF)
2. For each of top N results:
   - Extract snippet with ±context_lines
   - Create FileWindow with type="search_result"
   - Store metadata (search_score, query)
3. Store all, render, return window_ids
```

**Use Case:** Explore multiple implementations of a pattern simultaneously.

### Update Window

**Goal:** Adjust line range of existing window (re-snapshot from file).

```python
update_window(
    window_id="abc-123-def",
    start=100,
    end=120
)

# Implementation:
1. Find window by id
2. Re-read new range from file
3. Update window.content, start_line, end_line
4. Render to file
```

**Use Case:** Expand/shift visible range without closing/reopening.

### Close/Clear

**Goal:** Remove windows from view.

```python
close_window(window_id="abc-123-def")  # Remove one
clear_all_windows()                     # Remove all
```

**Use Case:** Free context for new windows, clean up after task completion.

---

## Rendering Format

Windows render to markdown with clear delimiters (like structural view):

```markdown
---FILE_WINDOWS
---FILE_WINDOW_abc-123-def
**file**: src/nabu/main.py
**lines**: 107-115 (9 lines)
**type**: frame_body
**frame**: nabu.parse_codebase

107:    def _collect_structural_info(self, frame: AstFrameBase, analysis: Dict[str, Any]) -> None:
108:        """Collect structural information for analysis."""
109:        from .core.frame_types import FrameNodeType
110:
111:        if frame.type == FrameNodeType.LANGUAGE and frame.language:
112:            if frame.language not in analysis['languages']:
113:                analysis['languages'].append(frame.language)
114:
115:        elif frame.type == FrameNodeType.PACKAGE and frame.qualified_name:
---FILE_WINDOW_abc-123-def_END

---FILE_WINDOW_xyz-789-ghi
**file**: src/nabu/core/frame_types.py
**lines**: 4-10 (7 lines)
**type**: search_result
**query**: "error handling"
**score**: 0.02

4: class FrameNodeType(Enum):
5:     # Structural frames - core hierarchy
6:     CODEBASE = "CODEBASE"
7:     LANGUAGE = "LANGUAGE"
8:     PACKAGE = "PACKAGE"
9:     CLASS = "CLASS"
10:    CALLABLE = "CALLABLE"
---FILE_WINDOW_xyz-789-ghi_END
---FILE_WINDOWS_END
```

**Properties:**
- Each window wrapped with unique delimiters (includes uuid)
- Metadata at top (file, lines, type, etc.)
- Line numbers preserved (absolute, not relative)
- Clear start/end boundaries

---

## Context Efficiency

**Traditional File Reading:**
```python
Read("entire_file.py")  # 500 lines
# → 500 lines in context
# → Used once, then lost
# → Re-read if needed again = waste
```

**With File Windows:**
```python
open_frame_window("SomeClass")           # 25 lines
open_frame_window("another_function")    # 15 lines
open_search_windows("error", max_windows=3)  # 30 lines total
# → 70 lines in context
# → Persistent across turns
# → No re-reading
# → Simultaneous visibility
```

**Benefits:**
- **Selective:** Only open what matters
- **Persistent:** Stays visible across conversation turns
- **Simultaneous:** See multiple locations at once
- **Efficient:** 5-10x fewer tokens than full file reads

---

## Integration with Structural View

**Complementary Workflows:**

**Pattern 1: Navigate → Open**
```
1. structural_view(operation="search", query="authentication")
2. Observe: AuthManager ● 0.03, LoginHandler ● 0.02
3. open_frame_window("AuthManager")
4. open_frame_window("LoginHandler")
5. Now see both implementations side-by-side
```

**Pattern 2: Explore → Drill**
```
1. structural_view(operation="expand", path="nabu.mcp.tools")
2. See: SearchTool, ShowStructureTool, ImpactTool
3. open_frame_window("SearchTool")
4. Examine implementation
5. open_range_window(file_path, 50, 75)  # Specific helper function
```

**Pattern 3: Search Both Layers**
```
1. structural_view(operation="search", query="error handling")
   → Get WHERE (classes/functions)
2. open_search_windows("error handling", max_windows=5)
   → Get WHAT (implementations with context)
3. Navigate between structure and content
```

**The Synergy:**
- Structural view: high-level map, navigation, relationships
- File windows: detailed content, implementations, comparisons
- Together: spatial awareness (where + what)

---

## Design Constraints & Decisions

### 1. Window Limits
**Decision:** No limit initially (0 = unlimited)
**Rationale:** Need usage data to tune optimal limit
**Future:** Dynamic limit based on total_lines (e.g., 500-1000 lines max)

### 2. Auto-Close Policy
**Decision:** No auto-close (manual only)
**Rationale:** User controls context, no surprising state changes
**Future:** Optional LRU eviction if limit exceeded

### 3. Context Lines (Search Results)
**Decision:** Default 3 lines before/after
**Rationale:** Matches common IDE peek definition behavior
**Configurable:** User can adjust per operation

### 4. File Updates
**Decision:** Snapshot on open (no file watching)
**Rationale:** Prototype simplicity, files rarely change mid-conversation
**Future:** Optional refresh operation or auto-detect on update

### 5. Operations
**Decision:** Always explicit (no auto-open from search)
**Rationale:** No tool bias, user controls when windows open
**Pattern:** Tool suggests, user decides

---

## Implementation Components

### Files to Create

```
src/nabu/tui/
├── file_window.py              # FileWindow dataclass
└── file_windows_manager.py     # FileWindowsManager class

src/nabu/mcp/tools/
└── file_windows_tool.py        # MCP tool

.nisaba/
└── file_windows.md             # Generated state file
```

### Files to Modify

```
src/nisaba/wrapper/proxy.py
└── Add file_windows_cache to AugmentInjector.__init__()
    self.file_windows_cache = FileCache(
        Path("./.nisaba/file_windows.md"),
        "file windows",
        "FILE_WINDOWS"
    )
    
└── Add to _inject_augments() body injection
    {self.file_windows_cache.load()}
```

---

## Tool Interface

**Tool name:** `file_windows`

**Operations:**

```python
# Open frame's full body
file_windows(
    operation="open_frame",
    frame_path="nabu.parse_codebase"
)

# Open specific line range
file_windows(
    operation="open_range",
    file_path="src/nabu/main.py",
    start=107,
    end=115
)

# Open search results
file_windows(
    operation="open_search",
    query="error handling database",
    max_windows=5,
    context_lines=3
)

# Update existing window
file_windows(
    operation="update",
    window_id="abc-123-def",
    start=100,
    end=120
)

# Close window
file_windows(
    operation="close",
    window_id="abc-123-def"
)

# Clear all windows
file_windows(
    operation="clear_all"
)

# Show current windows summary
file_windows(
    operation="status"
)
```

---

## Usage Patterns

### Comparing Implementations

```python
# Find all authentication methods
structural_view(operation="search", query="authentication validate")

# Open top 3 for comparison
file_windows(operation="open_search", query="authentication validate", max_windows=3)

# Now see all 3 side-by-side in system prompt
# Compare approaches, find patterns
```

### Understanding Dependencies

```python
# Navigate to class
structural_view(operation="expand", path="nabu.SearchTool")

# Open the class
file_windows(operation="open_frame", frame_path="nabu.SearchTool")

# See imports at top of file
file_windows(operation="open_range", file_path="src/nabu/search.py", start=1, end=20)

# Open a dependency
file_windows(operation="open_frame", frame_path="nabu.SearchBackend")

# Now see: SearchTool implementation + imports + dependency
```

### Bug Investigation

```python
# Search for error location
structural_view(operation="search", query="handle exception error")

# Open suspicious function
file_windows(operation="open_frame", frame_path="ErrorHandler.process")

# Open calling context (from stack trace)
file_windows(operation="open_range", file_path="src/app/main.py", start=450, end=475)

# Open error definitions
file_windows(operation="open_frame", frame_path="exceptions.ValidationError")

# All evidence visible simultaneously
```

---

## State Management

**In-Memory State:**
```python
manager.windows = {
    "abc-123": FileWindow(...),
    "def-456": FileWindow(...),
    "ghi-789": FileWindow(...)
}
```

**On-Disk State:**
```
.nisaba/file_windows.md  # Rendered markdown
```

**Proxy State:**
```python
injector.file_windows_cache.content  # Cached with mtime
```

**Lifecycle:**
1. Tool call → mutate manager.windows
2. Manager renders → write file
3. Proxy detects mtime → reloads cache
4. Next API call → injects to system prompt
5. Claude sees updated windows

**On MCP Restart:**
- In-memory state lost (manager.windows = {})
- File persists but ignored (stale)
- Windows cleared (start fresh)
- Acceptable for prototype

**Future Persistence:**
- Save windows dict to JSON
- Restore on restart
- Separate state from rendering

---

## Why This Works

**1. Proven Architecture**
- Identical pattern to structural view (battle-tested)
- Same injection mechanism (FileCache + proxy)
- Same state management (in-memory → file → context)

**2. Complements Existing Tools**
- Structural view: navigation graph
- File windows: content detail
- Search: both layers (structure + content)
- Together: complete picture

**3. Context Efficient**
- 5-10x fewer tokens than full file reads
- Persistent (no re-reading)
- Selective (only what matters)
- Cumulative (build context incrementally)

**4. Natural Workflow**
- Mirrors IDE usage (multiple tabs/splits)
- Explicit control (no magic)
- Iterative exploration (open as needed)
- Clean up (close when done)

**5. Prototype-Friendly**
- Simple snapshot model
- No complex watching/updates
- No limits to tune initially
- Low implementation risk

---

## Future Enhancements

**Phase 1 (Current):**
- Basic operations (open/update/close)
- Snapshot on open
- No limits
- Manual management

**Phase 2 (After Usage Data):**
- Dynamic line limits (total_lines < 1000)
- LRU eviction if over limit
- Auto-refresh on update
- State persistence across restarts

**Phase 3 (Advanced):**
- Diff views (show changes)
- Syntax highlighting in rendering
- Window groups (related windows)
- Smart context (auto-include imports/deps)
- Integration with show_structure (open from results)

---

## Mental Model

**File windows are peripheral vision for code.**

Just as structural view gives you spatial awareness of WHERE code lives in the graph, file windows give you persistent visibility of WHAT the code does.

Think of your context window as an IDE workspace:
- Structural view = project navigator (tree on the side)
- File windows = open editor tabs (multiple files visible)
- Together = full IDE experience

The windows persist across conversation turns, staying visible like IDE tabs. You navigate the structural view to find locations, open windows to see content, and build understanding incrementally without context explosion.

**Navigate the graph. See the code. Maintain awareness. 🖤**

---

**TOOLS:**
- mcp__nabu__structural_view (navigate to find code)
- file_windows (open/manage content windows) - *to be implemented*
- mcp__nabu__search (find across both layers)
- mcp__nabu__show_structure (frame details before opening)

**REQUIRES:**
- workflows/structural_view_navigation (companion workflow)

---

Clean. Simple. Persistent. Spatial. Sharp. Sexy. 🖤