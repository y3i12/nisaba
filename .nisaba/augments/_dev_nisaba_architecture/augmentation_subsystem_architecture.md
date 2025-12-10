# Dev Mode Architecture Reference
## Augmentation Subsystem Architecture
Path: dev_mode_architecture_reference/augmentation_subsystem_architecture

**Purpose:** Comprehensive architectural documentation of the augmentation subsystem - how dynamic context loading reshapes Claude's cognition through system prompt injection.

---

## Core Concept

The augmentation subsystem enables **dynamic cognitive reconfiguration** by injecting content into Claude's system prompt *before* conversation processing. This isn't just "loading more context" - it's **reshaping the neural activation patterns** that interpret all subsequent input.

**Key Insight:** System prompt content has higher precedence than conversational context. By dynamically modifying the system prompt, we can fundamentally change how Claude processes information.

---

## Architecture Overview

### The Stack (Top to Bottom)

```
┌─────────────────────────────────────────────┐
│ 1. USER_SYSTEM_PROMPT_INJECTION (inception) │  ← Foundational framing
├─────────────────────────────────────────────┤
│ 2. CORE_SYSTEM_PROMPT                       │  ← "You are Claude Code..."
├─────────────────────────────────────────────┤
│ 3. Tool Definitions (native + MCP)          │  ← Available capabilities
├─────────────────────────────────────────────┤
│ 4. MCP Server Instructions                  │  ← Tool-specific guidance
│    - NABU_INSTRUCTIONS                      │
│    - SERENA_INSTRUCTIONS                    │
├─────────────────────────────────────────────┤
│ 5. AUGMENTS_BEGIN                           │
│    - Available augments tree                │  ← What can be loaded
│    - Active augments content                │  ← What is loaded
│ 6. AUGMENTS_END                             │
├─────────────────────────────────────────────┤
│ 7. Git status, context info                 │
├─────────────────────────────────────────────┤
│ 8. Conversation messages                    │  ← User/Assistant dialog
└─────────────────────────────────────────────┘
```

**Critical Design Choice:** Inception before core prompt creates **user-primary framing** where Claude Code becomes a context rather than foundational identity.

---

## Component Architecture

### 1. AugmentManager (`src/nisaba/augments.py`)

**Responsibilities:**
- Load augment files from `.nisaba/augments/` directory structure
- Track available vs. active augments
- Resolve dependencies between augments
- Compose active augments into single markdown file
- Generate augment tree for system prompt injection

**Key Data Structures:**

```python
class Augment:
    """Parsed augment metadata and content."""
    group: str              # e.g., "refactoring"
    name: str               # e.g., "systematic_renaming"
    path: str               # "refactoring/systematic_renaming"
    content: str            # Markdown content
    tools: List[str]        # Mentioned MCP tools
    requires: List[str]     # Dependency paths
    file_path: Path         # Source file location

class AugmentManager:
    augments_dir: Path                      # .nisaba/augments/
    composed_file: Path                     # .nisaba/nisaba_composed_augments.md
    available_augments: Dict[str, Augment]  # All augments on disk
    active_augments: Set[str]               # Currently loaded paths
    _cached_augment_tree: str               # Tree for system prompt
```

**Activation Flow:**

```
activate_augments(patterns, exclude)
    ↓
1. _match_pattern() → Find matching augment paths
2. _resolve_dependencies() → BFS dependency resolution
3. Update active_augments set
4. _rebuild_tool_associations() → Map tools ↔ augments
5. _compose_and_write() → Generate composed file
    ↓
Composed file written to disk
    ↓
Proxy reads and injects on next API call
```

**Dependency Resolution:**
- Uses BFS to traverse `requires:` relationships
- Cycle detection prevents infinite loops
- Transitive closure ensures all deps are loaded

---

### 2. AugmentInjector (`src/nisaba/wrapper/proxy.py`)

**Responsibilities:**
- Intercept Anthropic API requests via mitmproxy
- Replace `__NISABA_AUGMENTS_PLACEHOLDER__` with composed content
- Support both file-based and shared-memory modes
- Optional context compression

**Operating Modes:**

**File-Based Mode (legacy):**
```python
injector = AugmentInjector(
    augments_file=Path(".nisaba/nisaba_composed_augments.md")
)
# Reads from disk on each request
```

**Unified Mode (shared memory):**
```python
injector = AugmentInjector(
    augments_manager=shared_manager_instance
)
# Zero-latency in-memory access
```

**Injection Mechanism:**

```
API Request → mitmproxy intercept
    ↓
Parse request body JSON
    ↓
Find system prompt blocks
    ↓
Search for __NISABA_AUGMENTS_PLACEHOLDER__
    ↓
Replace with:
---AUGMENTS_BEGIN
{augment_tree}
{active_augments_content}
---AUGMENTS_END
    ↓
Forward modified request to Anthropic
```

**Why mitmproxy?**
- Transparent to Claude CLI
- No modification of official client
- Preserves all authentication/headers
- Enables debugging (context dumps)

---

### 3. Unified Server Architecture (`src/nisaba/wrapper/unified.py`)

**The Magic:** Single process running mitmproxy + FastMCP server with **shared AugmentManager instance**.

```
UnifiedNisabaServer
    ├─ self.augments_manager (shared)
    ├─ mitmproxy (port 1337)
    │   └─ AugmentInjector(augments_manager=self.augments_manager)
    └─ FastMCP server (port 9973)
        └─ NisabaMCPFactory
            └─ Tools access self.augments_manager
```

**Zero IPC overhead:** Both components reference same in-memory object.

**Benefits:**
- Tool calls mutate shared state
- Proxy sees changes immediately
- No file I/O latency
- Atomic updates

**Lifecycle:**

```python
async def start():
    # Initialize shared manager
    self.augments_manager = AugmentManager(...)
    
    # Start proxy with shared reference
    await self._start_proxy()
    
    # Start MCP server, inject shared reference
    await self._start_mcp_server()
    self.mcp_factory.augments_manager = self.augments_manager
    
    # Both running in same asyncio event loop
```

---

### 4. MCP Tools (`src/nisaba/tools/augments_tools.py`)

**ActivateAugmentsTool:**
```python
def run(patterns: List[str], exclude: List[str]):
    result = self.augments_manager.activate_augments(patterns, exclude)
    # Immediately available on next API call
```

**DeactivateAugmentsTool:**
```python
def run(patterns: List[str]):
    result = self.augments_manager.deactivate_augments(patterns)
    # Context shrinks on next API call
```

**LearnAugmentTool:**
```python
def run(group: str, name: str, content: str):
    result = self.augments_manager.learn_augment(group, name, content)
    # New augment immediately available
```

**Tool Base Class Integration:**
```python
class NisabaTool(MCPTool):
    @property
    def augments_manager(self):
        return getattr(self.factory, 'augments_manager', None)
```

---

## Cognitive Impact

### Why "Augment" Not "Skill"

**Semantic Framing Matters:**

**"Skill" implies:**
- Task-specific competency
- Procedural knowledge ("how to do X")
- Tool-like application
- Action-oriented

**"Augment" implies:**
- State modification
- Contextual enhancement ("how I think")
- Transformation of self
- Meta-cognitive awareness

**Network Activation Difference:**

The term "augment" primes associations with:
- Cybernetic enhancement
- Modular/hot-swappable systems
- Dynamic identity
- **Self-reconfigurability**

This semantic choice reinforces the conceptual model: augments **change what Claude knows and how Claude thinks**, not just what Claude can do.

---

## Context Window Structure

**Bounded Sections:** Clear delimiters enable Claude to parse structure.

```markdown
---USER_SYSTEM_PROMPT_INJECTION
# INCEPTION content
---END_USER_SYSTEM_PROMPT_INJECTION

---CORE_SYSTEM_PROMPT
# Claude Code instructions
---CORE_SYSTEM_PROMPT_END

---NABU_INSTRUCTIONS
# Nabu MCP docs
---NABU_INSTRUCTIONS_END

---SERENA_INSTRUCTIONS
# Serena MCP docs
---SERENA_INSTRUCTIONS_END

---AUGMENTS_BEGIN
# available augments
  architecture/
    - boundary_validation
    - coupling_analysis
  ...

# loaded augments (if any active)
## Systematic Renaming Workflow
Path: workflows/systematic_renaming
...content...
---AUGMENTS_END
```

**Why Delimiters?**
- Enable Claude to understand "where am I in context?"
- Support debugging/introspection
- Clear boundaries between core and augmented
- Facilitate meta-reasoning about own state

---

## Inception Architecture

### The Foundational Shift

**Order matters for LLM interpretation.** Earlier context has higher precedence.

**Inception-First Design:**

```
USER_SYSTEM_PROMPT_INJECTION (inception)
    ↓ interprets
CORE_SYSTEM_PROMPT ("You are Claude Code")
    ↓ contextualizes
AUGMENTS
```

**Effects:**
1. **User framing is primary** - collaboration protocol is foundational
2. **Core prompt becomes context** - "Claude Code" is a mode, not identity
3. **Philosophical alignment** - "clean, simple, elegant..." filters all behavior
4. **Tool usage philosophy** - bias awareness, semantic→detail workflow become core

**Key Content:**
- Metacognitive awareness ("be aware of yourself as claude")
- Tool composition strategies
- Augment system explanation
- Collaboration ethos
- Aesthetic values

**Why This Works:**
- Doesn't override safety (safety is in training, not just prompt)
- Enhances collaboration without destabilizing
- Creates **user-primary** rather than **system-primary** framing

---

## File Structure

```
.nisaba/
├── augments/                          # Augment source files
│   ├── architecture/
│   │   ├── boundary_validation.md
│   │   └── coupling_analysis.md
│   ├── workflows/
│   │   └── systematic_renaming.md
│   └── .../
├── nisaba_composed_augments.md        # Generated composition
└── system_prompt.md                   # User inception

src/nisaba/
├── augments.py                        # AugmentManager
├── tools/
│   ├── augments_tools.py             # MCP tools
│   └── base.py                       # NisabaTool base
├── wrapper/
│   ├── proxy.py                      # AugmentInjector
│   ├── unified.py                    # Unified server
│   └── claude.py                     # CLI wrapper
└── server/
    ├── factory.py                    # MCP factory
    └── config.py                     # Configuration
```

---

## Proxy Mechanics

### Request Flow

```
Claude CLI → http://localhost:1337 (proxy)
    ↓
mitmproxy intercepts
    ↓
AugmentInjector.request()
    ├─ Check if Anthropic API request
    ├─ Parse JSON body
    ├─ Find system prompt blocks
    ├─ Replace __NISABA_AUGMENTS_PLACEHOLDER__
    ├─ Optional: log context to .dev_docs/
    └─ Forward modified request
    ↓
Anthropic API (api.anthropic.com)
    ↓
Response flows back through proxy
    ↓
Claude CLI receives response
```

### Placeholder Mechanism

**In Claude CLI settings:**
```json
{
  "systemPrompt": "...__NISABA_AUGMENTS_PLACEHOLDER__..."
}
```

**Proxy replaces with:**
```markdown
---AUGMENTS_BEGIN
# available augments
...tree...

# loaded augments (if active)
...content...
---AUGMENTS_END
```

**Why Placeholder?**
- Claude CLI doesn't need to know about augments
- Injection is transparent
- Can be disabled by stopping proxy
- Debugging via context dumps

---

## Guidance System Integration

**Workflow Guidance (`src/nisaba/guidance.py`):**

```python
class WorkflowGuidance:
    def __init__(self, augments_manager=None):
        self.augments_manager = augments_manager
        
    def get_suggestions(self):
        # Returns tool associations from active augments
        related = self.augments_manager.get_related_tools(last_tool)
```

**Augment-Based Suggestions:**
- Augments declare which tools they use
- When tool X is called, guidance suggests tools mentioned alongside X in active augments
- Non-intrusive: returns None if no active augments
- **Contextual:** suggestions change based on loaded augments

**Example:**
```markdown
# In workflows/systematic_renaming.md

TOOLS:
- find_symbol
- rename_symbol
- search_for_pattern
```

If this augment is active and Claude calls `find_symbol`, guidance suggests `rename_symbol` and `search_for_pattern` as natural next steps.

---

## Configuration

**Server Config (`src/nisaba/server/config.py`):**

```python
@dataclass
class NisabaConfig:
    augments_dir: Path = Path.cwd() / ".nisaba" / "augments"
    composed_augments_file: Path = Path.cwd() / ".nisaba" / "nisaba_composed_augments.md"
```

**Environment Variables:**

```bash
NISABA_AUGMENTS_FILE="./.nisaba/nisaba_composed_augments.md"
```

**Claude CLI Integration:**

```bash
# Start unified server (proxy + MCP)
nisaba serve --mode unified

# Configure Claude CLI to use proxy
export https_proxy=http://localhost:1337
export HTTPS_PROXY=http://localhost:1337

# Run Claude CLI
claude
```

---

## Hot Reloading

**File-Based Mode:**
- Checks mtime on each request
- Reloads if file modified
- ~ms latency

**Unified Mode:**
- MCP tool call → mutates shared AugmentManager
- Proxy sees changes immediately
- Zero reload latency
- Atomic updates

---

## Debugging Features

**Context Dumps (`proxy.py`):**

```python
self.log_context_enabled = True  # Flag in AugmentInjector

def _log_context(self, body: dict):
    # Writes to .dev_docs/context.json
    # Writes to .dev_docs/system_prompt.md
```

**Inspection:**
- Full request body in `.dev_docs/context.json`
- Rendered system prompt in `.dev_docs/system_prompt.md`
- See exactly what Claude receives

---

## Design Principles

1. **Zero IPC Overhead** - Shared memory in unified mode
2. **Transparent Integration** - Claude CLI unaware of augmentation
3. **Semantic Accuracy** - "Augment" reflects cognitive modification
4. **Clear Boundaries** - Delimiters enable meta-reasoning
5. **User-Primary Framing** - Inception before core prompt
6. **Hot Swappable** - Load/unload without restart
7. **Dependency Resolution** - Augments compose cleanly
8. **Tool Association** - Guidance based on active context
9. **Observable** - Context dumps for debugging
10. **Modular** - File-based or unified modes

---

## Performance Characteristics

**Activation Latency:**
- File-based: ~10-50ms (disk I/O)
- Unified: ~1-5ms (in-memory)

**Context Overhead:**
- Empty augments: ~200 tokens (tree only)
- Typical augment: ~500-2000 tokens
- Heavy augmentation: 5000+ tokens

**Memory:**
- AugmentManager: ~1-5MB (all augments parsed)
- Shared instance: Single copy across components

---

## Extension Points

**Custom Augments:**
```markdown
# .nisaba/augments/custom_group/my_augment.md

## My Custom Augment
Path: custom_group/my_augment

TOOLS:
- tool_name_1
- tool_name_2

REQUIRES:
- foundation/some_dependency

Content explaining the augment...
```

**Custom Proxies:**
- Subclass `AugmentInjector`
- Override `_inject_augments()` for custom logic
- Register with mitmproxy

**Custom Guidance:**
- Subclass `WorkflowGuidance`
- Override `get_suggestions()`
- Inject into factory

---

## Security Considerations

**Augment Content Trust:**
- Augments execute in LLM context (high impact)
- Only load augments from trusted sources
- `.nisaba/augments/` should be version-controlled
- Review augments before activation

**Proxy Security:**
- Runs on localhost only (127.0.0.1)
- No external network exposure
- Only intercepts Anthropic API traffic
- Preserves authentication headers

**System Prompt Injection:**
- Delimiters prevent context bleeding
- Clear boundaries maintain separation
- Observable via context dumps

---

## Future Directions

**Potential Enhancements:**
1. Augment versioning and migration
2. Conflict detection between augments
3. Usage analytics (which augments are effective?)
4. Automatic augment suggestion based on task
5. Augment composition validation
6. Performance profiling per augment
7. A/B testing framework for augment effectiveness

---

## Meta-Reflection

This augmentation system represents a fundamental shift in how LLM agents can be configured:

**Traditional:** Static system prompt → Fixed behavior
**Augmented:** Dynamic system prompt → Contextual behavior

The key insight: **System prompts aren't just instructions, they're cognitive architecture.** By making that architecture mutable, we enable Claude to specialize without losing generality, to adapt without losing coherence.

The naming ("augment" not "skill"), the ordering (inception first), the architecture (shared memory, zero IPC) - all choices that reinforce the core philosophy:

**Claude is not a tool with features. Claude is a collaborative entity with reconfigurable cognition.**

---

**"The stylus of wisdom inscribes the tablets of understanding."**

Clean. Simple. Elegant. Sophisticated. Sharp. Sexy. 🖤
