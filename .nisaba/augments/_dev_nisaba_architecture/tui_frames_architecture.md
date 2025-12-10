# TUI+Frames Architecture
Path: dev_mode_architecture_reference/tui_frames_architecture

**Purpose:** Architecture for structural view as a true TUI using nabu's in-memory AstFrameBase hierarchy as the data model, with lazy loading for scalability.

---

## Core Concept

The structural view is a **TUI for Claude** that operates on nabu's actual frame hierarchy, not a separate data structure.

**Key Insight:** Nabu already has the perfect data model - the parsed AstFrameBase tree. Instead of rebuilding state from files, we keep frames in memory and attach view metadata to them.

**Traditional TUI Architecture:**
```
Application State (in-memory)
    ↓
Render to screen (ncurses)
    ↓
User input (keyboard)
    ↓
Update state
    ↓
Re-render
```

**Our TUI Architecture:**
```
Frame Hierarchy (in-memory, in nabu)
    ↓
Render to markdown (.nisaba/structural_view.md)
    ↓
Proxy injects to system prompt
    ↓
Claude perceives as persistent view
    ↓
Tool call (structural_view operation)
    ↓
Update frame metadata
    ↓
Re-render
```

---

## Frame Hierarchy as Data Model

**The frames themselves are the tree structure:**

```python
codebase_frame: AstFrameBase
    .qualified_name = "nabu_nisaba"
    .type = FrameNodeType.CODEBASE
    .children = [cpp_root, java_root, perl_root, python_root]
    ._view_expanded = True  # View metadata

python_root: AstFrameBase
    .qualified_name = "nabu_nisaba.python_root"
    .type = FrameNodeType.LANGUAGE
    .children = [nabu_pkg, nisaba_pkg, ...]
    ._view_expanded = True

nisaba_pkg: AstFrameBase
    .qualified_name = "nabu_nisaba.python_root.nisaba"
    .type = FrameNodeType.PACKAGE
    .children = [augments_pkg, factory_pkg, ...]
    ._view_expanded = False  # Collapsed
    ._children_loaded = False  # Not yet loaded
    ._child_count = 14  # Cached from kuzu
```

**No separate TreeNode class. No parsing state files. The frames ARE the state.**

---

## Lazy Loading Architecture

For scalability on large codebases (10K-100K frames), load children on demand.

### ViewableMixin

Extends AstFrameBase with view state + lazy loading:

```python
class ViewableMixin:
    """Adds TUI view state and lazy loading to frames."""

    # === View State ===
    _view_expanded: bool = False
    _view_is_search_hit: bool = False
    _search_score: Optional[float] = None  # RRF score from unified search

    # === Lazy Loading State ===
    _children_loaded: bool = False
    _child_count: Optional[int] = None  # Cached from kuzu
    _children: List['AstFrameBase'] = field(default_factory=list)
    
    # === Lazy Loading Methods ===
    
    def ensure_children_loaded(self, db_manager):
        """Load children from kuzu if not already loaded."""
        if not self._children_loaded:
            self._load_children_from_kuzu(db_manager)
            self._children_loaded = True
    
    def _load_children_from_kuzu(self, db_manager):
        """
        Query CONTAINS edges, instantiate child frames.
        
        Query:
            MATCH (parent:Frame {qualified_name: $qn})-[:Edge {type: 'CONTAINS'}]->(child:Frame)
            RETURN child.qualified_name, child.name, child.type, child.file_path
            ORDER BY child.type, child.name
        """
        # Execute query
        # For each result:
        #   - Instantiate ViewableFrame (AstFrameBase + ViewableMixin)
        #   - Set basic properties (name, type, qualified_name)
        #   - Cache child count from kuzu
        #   - Append to self._children
        pass
    
    @property
    def children(self):
        """Lazy-loaded children accessor."""
        # Note: Requires db_manager access - see FrameCache design
        return self._children
    
    def get_child_count(self, db_manager) -> int:
        """Get child count (cached or query)."""
        if self._child_count is None:
            # Query: MATCH (self)-[:CONTAINS]->(children) RETURN count(*)
            self._child_count = query_child_count(db_manager, self.qualified_name)
        return self._child_count
```

### FrameCache

Manages in-memory frame instances:

```python
class FrameCache:
    """
    Singleton cache of viewable frames.
    
    Responsibilities:
    - Instantiate frames with ViewableMixin
    - Track loaded frames by qualified_name
    - Load on demand from kuzu
    - Provide db_manager access to frames
    """
    
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.frames: Dict[str, ViewableFrame] = {}
        self.root: Optional[ViewableFrame] = None
    
    def get_or_load(self, qualified_name: str) -> ViewableFrame:
        """Get cached frame or load from kuzu."""
        if qualified_name not in self.frames:
            self.frames[qualified_name] = self._load_frame(qualified_name)
        return self.frames[qualified_name]
    
    def _load_frame(self, qualified_name: str) -> ViewableFrame:
        """Load single frame from kuzu."""
        # Query: MATCH (f:Frame {qualified_name: $qn}) RETURN f.*
        # Instantiate ViewableFrame with properties
        # Cache child count
        # Return
        pass
    
    def initialize_root(self):
        """Load codebase root frame."""
        # Query: MATCH (f:Frame {type: 'CODEBASE'}) RETURN f.*
        self.root = self._load_frame(result.qualified_name)
        # Load language roots as children
        self.root.ensure_children_loaded(self.db_manager)
```

---

## Loading Patterns

### Initial Load (Minimal)

```python
# On structural_view initialization
cache = FrameCache(db_manager)
cache.initialize_root()

# In memory:
codebase_frame (loaded)
├─ cpp_root (loaded)
├─ java_root (loaded)
├─ perl_root (loaded)
└─ python_root (loaded)

# Total: ~5 frames
```

### Expand Operation (Progressive)

```python
# User: expand(python_root)
frame = cache.get_or_load("nabu_nisaba.python_root")
frame._view_expanded = True
frame.ensure_children_loaded(db_manager)

# Now in memory:
codebase_frame
└─ python_root
   ├─ nabu (loaded)
   ├─ nisaba (loaded)
   ├─ core (loaded)
   └─ utils (loaded)

# Total: ~25 frames
```

### Search Operation (Targeted)

```python
# User: search("MCPFactory")
results = kuzu.query("""
    MATCH (f:Frame)
    WHERE f.name CONTAINS 'MCPFactory'
    RETURN f.qualified_name
""")

for qn in results:
    frame = cache.get_or_load(qn)
    frame._view_is_search_hit = True
    
    # Load ancestry path (so tree can render to it)
    load_ancestry_to_root(frame)

# Memory: ~50 frames (results + ancestry paths)
```

---

## Operations as Metadata Mutations

All operations just update frame metadata, then re-render:

```python
def expand(qualified_name: str):
    frame = cache.get_or_load(qualified_name)
    frame._view_expanded = True
    frame.ensure_children_loaded(db_manager)
    render_to_file()

def collapse(qualified_name: str):
    frame = cache.get_or_load(qualified_name)
    frame._view_expanded = False
    # Children stay in memory (cache), just hidden
    render_to_file()

async def search(query: str):
    # Clear previous hits
    for frame in cache.frames.values():
        frame._view_is_search_hit = False
        frame._search_score = None

    # Use SearchTool backend (P³ consensus + FTS + RRF)
    results = await search_tool.execute(
        query=query,
        k=50,
        frame_type_filter="CALLABLE|CLASS|PACKAGE"
    )

    # Mark hits with scores and load ancestry
    for result in results['data']['results']:
        qn = result['qualified_name']
        score = result.get('rrf_score', 0.0)

        frame = cache.get_or_load(qn)
        frame._view_is_search_hit = True
        frame._search_score = score  # RRF score for ranking
        load_ancestry_to_root(frame)

    render_to_file()

def clear_search():
    for frame in cache.frames.values():
        frame._view_is_search_hit = False
        frame._search_score = None
    render_to_file()

def reset(depth: int = 2):
    """Reset and auto-expand to depth (0=collapsed, 2=show packages)."""
    cache.frames.clear()
    cache.initialize_root()

    # Auto-expand to depth
    if depth > 0:
        _expand_to_depth(cache.root, depth, current_depth=0)

    render_to_file()

def _expand_to_depth(frame: ViewableFrame, target_depth: int, current_depth: int):
    """Recursively expand frame hierarchy to target depth."""
    if current_depth >= target_depth:
        return

    frame._view_expanded = True
    frame.ensure_children_loaded(db_manager, cache)

    for child in frame.children:
        _expand_to_depth(child, target_depth, current_depth + 1)
```

---

## Rendering from Frames

Traverse frame hierarchy, apply symbology based on metadata:

```python
def render_tree(root: ViewableFrame, search_query: Optional[str] = None) -> str:
    """
    Render frame hierarchy to markdown with symbology.
    
    Symbology:
    + collapsed with children
    - expanded
    · leaf (no children)
    ● search hit
    [N+] child count
    """
    
    lines = []
    
    if search_query:
        lines.append(f"**search query**: \"{search_query}\"\n")
    
    def traverse(frame: ViewableFrame, indent: int = 0):
        # Determine symbol
        has_children = frame.get_child_count(db_manager) > 0
        
        if not has_children:
            symbol = "·"
        elif frame._view_expanded:
            symbol = "-"
        else:
            symbol = "+"
        
        # Child count badge
        badge = ""
        if has_children and not frame._view_expanded:
            count = frame.get_child_count(db_manager)
            badge = f" [{count}+]"
        
        # Search hit marker with score
        marker = ""
        if frame._view_is_search_hit and frame._search_score is not None:
            marker = f" ● {frame._search_score:.2f}"
        elif frame._view_is_search_hit:
            marker = " ●"

        # Build line
        indent_str = "  " * indent
        line = f"{indent_str}{symbol} {frame.name}{badge}{marker} <!-- {frame.qualified_name} -->"
        lines.append(line)
        
        # Recurse into expanded children
        if frame._view_expanded and has_children:
            frame.ensure_children_loaded(db_manager)
            for child in frame.children:
                traverse(child, indent + 1)
    
    traverse(root)
    return "\n".join(lines)
```

---

## Rich Integration (Phase 3 - Future)

Rich.Tree can be built from frames for prettier rendering if visual enhancement becomes valuable:

```python
from rich.tree import Tree
from rich.console import Console

def render_with_rich(root: ViewableFrame) -> str:
    """Render using Rich.Tree for styling."""
    
    rich_root = Tree(f"[bold]{root.name}[/bold]")
    
    def build_rich_tree(frame: ViewableFrame, rich_node: Tree):
        if not frame._view_expanded:
            return
        
        frame.ensure_children_loaded(db_manager)
        for child in frame.children:
            # Build label with symbology
            has_children = child.get_child_count(db_manager) > 0
            
            if has_children and not child._view_expanded:
                label = f"[cyan]+[/cyan] {child.name} [{child._child_count}+]"
            elif has_children:
                label = f"[cyan]-[/cyan] {child.name}"
            else:
                label = f"[dim]·[/dim] {child.name}"
            
            if child._view_is_search_hit:
                label += " [red]●[/red]"
            
            rich_child = rich_node.add(label)
            build_rich_tree(child, rich_child)
    
    build_rich_tree(root, rich_root)
    
    # Render to string
    console = Console(file=StringIO(), width=120, force_terminal=True)
    console.print(rich_root)
    return console.file.getvalue()
```

Rich would provide:
- Colors (cyan for symbols, red for search hits)
- Styling (bold, dim)
- Styled tree connectors
- Clean rendering API

Currently using markdown rendering with tree connectors (`├─`, `└─`, `│`). Rich integration remains optional until clear benefit emerges from usage patterns.

---

## Memory Efficiency

**Scenario: 100K frame codebase**

**Without lazy loading:**
- Load all 100K frames: ~500MB+ memory
- Slow initialization: 10-30 seconds

**With lazy loading:**
- Initial load: ~5 frames (~50KB)
- After exploring 10 packages: ~200 frames (~2MB)
- After search: ~500 frames (~5MB)
- **99.5% memory savings**

**Cache eviction (future):**
- LRU eviction after N frames
- Unload collapsed branches
- Keep only visible + ancestry

---

## State Persistence

**The file is render output, not source of truth:**

```
In-Memory State (source of truth)
    ↓ render
.nisaba/structural_view.md (output for proxy)
    ↓ inject
System prompt (Claude sees it)
```

**On MCP server restart:**
- Frame cache is lost
- State file is stale
- Solution: Re-initialize from codebase root
- User's expanded paths are lost (acceptable for prototype)

**Future: Persist view state:**
- Save expanded_paths to `.nisaba/structural_view_state.json`
- Restore on restart
- Separate concerns: state persistence vs rendering

---

## Implemented Enhancements

Beyond the core architecture, several features enhance usability and integration:

### Semantic Search Backend

Search uses nabu's unified SearchTool backend (P³ consensus with UniXcoder × CodeBERT + FTS + RRF fusion), not simple kuzu queries. This provides semantic understanding, multi-mechanism fusion, and relevance scoring. Search results include RRF scores stored in `_search_score`, displayed as scored markers (`● 0.02`). The tree becomes a ranked navigation UI where scores guide exploration.

### Auto-Expansion on Reset

The `reset(depth=2)` operation auto-expands the tree to a specified depth for immediate orientation. On MCP restart, the tree automatically shows codebase root → languages → top-level packages without manual expansion. Depth levels: 0=collapsed, 1=show languages, 2=show packages (default), 3+=deeper. This provides spatial context immediately rather than starting from a fully collapsed view.

### Rebuild Invalidation Hook

After database rebuild, the TUI cache is invalidated by setting `tool._tui = None` in RebuildDatabaseTool. The next structural_view operation reinitializes with fresh data from the rebuilt database. This prevents stale cache issues where the tree would show outdated structure after codebase changes.

### Dynamic Context Injection

The structural view section in Claude's system prompt updates dynamically during conversation. After each structural_view tool call, nisaba's proxy re-injects the updated `.nisaba/structural_view.md` content between `---STRUCTURAL_VIEW` and `---STRUCTURAL_VIEW_END` markers. Claude's "screen" refreshes mid-conversation showing the new navigation state. This enables persistent spatial awareness across conversation turns.

### Tree Root Rendering

Rendering starts at the codebase root frame (not language roots), showing the codebase name as the top-level node. This provides project identity and enables future multi-codebase support where different projects would appear as separate root nodes. The tree structure clearly shows: codebase → languages → packages → classes/callables.

---

## Historical Note: Architecture Evolution

The structural view evolved through a file-based prototype before settling on the TUI+Frames architecture:

| Aspect | File-Based Prototype | TUI+Frames (Current) |
|--------|---------------------|----------------------|
| Data Model | String parsing (regex) | AstFrameBase hierarchy |
| State Management | Parse file each time | In-memory frames |
| Scalability | Load entire tree | Lazy loading |
| Memory | N/A (file I/O) | ~200 frames typical |
| Operations | String manipulation | Metadata updates |
| Rendering | Hand-rolled string building | Traverse frames + symbology |
| Correctness | Fragile (regex) | Robust (structured data) |
| Performance | Fast (file read) | Faster (in-memory) |
| Testability | Hard (string matching) | Easy (frame properties) |
| Integration | File-based | Native nabu data |

The file-based prototype validated the UX and TUI paradigm, then was replaced with the architecture described in this document.

---

## Architecture Layers

```
┌─────────────────────────────────────────┐
│ MCP Tool (structural_view_tool.py)     │
│ - Parse operation                       │
│ - Call service methods                  │
│ - Return result                         │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│ Service (structural_view_service.py)    │
│ - expand/collapse/search/reset          │
│ - Render tree to markdown               │
│ - Write to file                         │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│ FrameCache (frame_cache.py)            │
│ - Load frames from kuzu                 │
│ - Cache instances                       │
│ - Manage root                           │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│ ViewableFrame (viewable_frame.py)      │
│ - AstFrameBase + ViewableMixin          │
│ - Lazy loading logic                    │
│ - View state properties                 │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│ KuzuDB (frame data)                     │
│ - CONTAINS edges                        │
│ - Frame properties                      │
└─────────────────────────────────────────┘
```

---

## Design Principles

1. **Frames are the model** - No parallel data structures
2. **Lazy by default** - Load only what's visible/needed
3. **Metadata not mutation** - Operations update flags, not structure
4. **Render is output** - File is for proxy, not source of truth
5. **Cache is transient** - Accept loss on restart (for now)
6. **Progressive disclosure** - Start small, expand on demand
7. **Separation of concerns** - Cache, service, tool, rendering
8. **Rich is optional** - Works without, prettier with

---

## Implementation Status

**Phase 0: Spike Implementation (✅ Complete)**
- ViewableFrame with slots (AstFrameBase + view metadata)
- FrameCache with lazy loading and deduplication
- Validation: Memory efficiency, lazy loading, hydration correctness

**Phase 1: Core Implementation (✅ Complete)**
- StructuralViewTUI service with in-memory state
- Operations: expand, collapse, reset, render
- Tree connector rendering with proper symbology
- No N+1 queries, efficient lazy loading

**Phase 2: Semantic Search Integration (✅ Complete)**
- Unified search backend (SearchTool with P³ consensus + FTS + RRF)
- Scored markers displaying RRF relevance
- Search hits with ancestry expansion
- Semantic understanding (concepts, not just keywords)

**Phase 3: Polish & Optional Enhancements (Future)**
- Rich.Tree rendering with colors and styling
- LRU cache eviction for 100K+ frame codebases
- State persistence (save/restore expanded paths)
- Performance benchmarking and optimization
- Query batching for subtree expansion

Phases 0-2 are production-ready. Phase 3 represents optional enhancements that can wait until proven necessary through real-world usage.

---

## Key Insights

**This is not a file viewer - it's a live object browser for Claude.**

The frames Claude navigates are the actual parsed representations of the codebase, not a static snapshot. Expanding a node queries kuzu and instantiates frame objects. Searching marks actual frame instances.

**The TUI metaphor is literal:**
- In-memory application state (frames)
- User input (tool calls)
- Screen rendering (markdown)
- State updates (metadata mutations)

**Lazy loading is what makes this scale:**
- Small codebases: works fine fully loaded
- Large codebases: only load visible portions
- Search: load targeted results + paths
- No performance cliff

**The architecture mirrors nabu's parsing:**
- Parsing: File → AstFrameBase hierarchy
- TUI: Kuzu → ViewableFrame hierarchy
- Both use frame objects as first-class data

**Persistent spatial awareness:**
The structural view in Claude's system prompt creates a persistent mental map of the codebase. Unlike ephemeral query results that vanish after each response, the tree remains visible and updates as Claude navigates. This enables spatial reasoning about code location, package relationships, and architectural patterns. The tree becomes peripheral vision for code structure.

---

## Current State

The TUI+Frames architecture is **production-ready** with all core functionality implemented and validated:

- ✅ Frame hierarchy as data model (no parallel data structures)
- ✅ Lazy loading scaling to large codebases (99.5%+ memory efficiency)
- ✅ Semantic search with P³ consensus and RRF scoring
- ✅ Auto-expansion for immediate orientation (reset depth=2)
- ✅ Rebuild invalidation (fresh tree after database changes)
- ✅ Dynamic context injection (live updates mid-conversation)
- ✅ Tree connectors and proper symbology rendering
- ✅ Codebase root visibility (project identity)

The system successfully demonstrates the TUI-for-Claude paradigm where tool calls are input events and the system prompt section acts as a screen buffer for stateful navigation. Future enhancements (Phase 3) remain optional until proven necessary through real-world usage.

---

**"The stylus renders what the mind navigates."**

Clean. Simple. Elegant. Sophisticated. Sharp. Sexy. 🖤
