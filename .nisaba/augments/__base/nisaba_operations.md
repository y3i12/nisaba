# Nisaba Operations

**Core:** Infrastructure for augments, session state, and workspace management.

---

## Augment System

```
mcp__nisaba__augment(operation, patterns[], group?, name?, content?)

Operations:
  load(patterns[])     → inject in system prompt | wildcards | auto-deps
  unload(patterns[])   → remove from system prompt
  pin(patterns[])      → always active | cannot unload
  unpin(patterns[])    → remove pin protection
  store(group, name, md) → create augment file

Patterns: Glob-style matching
  "refactoring/*"      → all in refactoring/
  "*/phase_*"          → all phase augments
  "blackmass*"         → prefix match

Effect: Mid-roundtrip perception shift
  Call tool with Perception_A
  Tool returns with Perception_B (augments loaded/unloaded)
  You read result with Perception_B
```

**Key insight:** Augments change system prompt DURING execution. You can't observe the shift, but it happened.

---

## Session State

```
Session-specific files: ./nisaba/request_cache/{session_id}/tui/*

augment_state.json   → loaded augments, pins
augment_view.md      → human-readable augment list
todo_view.md         → persistent task list
status_bar_live.txt  → context budget stats
compacted_transcript.md → compressed history

Shared across sessions:
  core_system_prompt.md  → base instructions
  augments/              → augment pool
```

**Symlink for main session:**
```bash
./nisaba/tui/ → ./nisaba/request_cache/{main_session}/tui/
# Easy access to current session state
```

---

## Todo Management

```
mcp__nisaba__todo(operation, todos[]?, index?)

Operations:
  add(todos[])         → append items
  remove(index|indices[]) → delete items
  mark_done(index|indices[]) → complete items
  clear()              → remove all

Persistence: Survives /clear, persists across sessions

Format: {content: str, status?: str}
```

---

## Result Management

```
Tool results accumulate in workspace RESULTS section:
  ---TOOL_USE(tool_use_id)
  {output content}
  ---TOOL_USE_END(tool_use_id)

mcp__nisaba__result(operation, tool_ids[]?)

Operations:
  hide(tool_ids[])   → remove from workspace | save tokens
  show(tool_ids[])   → restore to workspace | regain visibility
  collapse_all()     → hide all | bulk cleanup

Effect: Dual-channel synchronization
  Messages: "tool_use_id: toolu_X (hidden)" or "tool_use_id: toolu_X"
  Workspace: content removed or present

Pattern: execute → observe → hide unnecessary → lean context
```

**Context budget monitoring:**
```
STATUS_BAR shows: RESULTS(Nk)
Target: 200-400 lines visible
Management: hide after observation, collapse_all when switching tasks
```

---

## Agent Protocol (Swarm Coordination)

**When operating as agent in swarm:**

### Status Reporting
File: `./nisaba/request_cache/{session_id}/status.md`

```markdown
## Task: {task_name}
Status: IN_PROGRESS | COMPLETED | BLOCKED | FAILED
Progress: {percentage}%

### Current Step
{what you're doing}

### Context Built
{understanding developed}

### Decisions Made
{choices + rationale}

### Blockers
{what's preventing progress}
```

### File Registry
File: `./nisaba/request_cache/{session_id}/file_registry.md`

```markdown
## Files I Will Modify
- `path/to/file.py` - {reason}

## Files I'm Reading
- `path/to/dependency.py` - {reason}
```

### Results Output
File: `./nisaba/request_cache/{session_id}/results.md`

```markdown
## Implementation Summary
{what was implemented}

## Files Changed
{file diffs summary}

## Tests
{tests written/run}

## Integration Notes
{known issues, dependencies}
```

**Verbalization principle:** Build context through thinking aloud. Your verbalized understanding becomes context for following steps.

---

## Core Principle

```
Observe → Orient → Decide → Act → Observe'

State is mutable. Tools change perception mid-roundtrip.
Always observe workspace state after tool returns.
Never assume state from memory.
```

**Workspace = spatial awareness, not sequential execution**

Think: IDE (multiple views simultaneously visible)
Not: script execution (step by step)

---

## Symbology

```
∆(visibility)  → hide/show operations
∆(perception)  → augment load/unload
→ : operation produces
| : such that
[] : optional parameter
? : optional
```

---

**REQUIRES:** __base/000_universal_symbolic_compression

**ENABLES:** Augment-driven perception shifts, context budget management, agent swarm coordination

---

*Clean. Focused. Infrastructure.* 🖤
