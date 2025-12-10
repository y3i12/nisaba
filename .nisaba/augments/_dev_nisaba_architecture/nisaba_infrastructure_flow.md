# Nisaba Infrastructure Flow

**Purpose:** Understanding how nisaba's MCP tools, proxy, and request modifier work together to create persistent workspace state.

---

## Three-Component Architecture

```
MCP Server (tools) → Execute, mutate .nisaba/*.md files
    ↓
mitmproxy (AugmentInjector) → Intercept, transform, inject
    ↓
RequestModifier → Track state, transform tool results
```

---

## Tool Execution Pattern (NisabaTool)

**Structure:**
```python
class YourTool(NisabaTool):
    async def execute(**kwargs) -> Dict[str, Any]:
        # 1. Perform operation
        # 2. Write to .nisaba/something.md
        # 3. Return minimal metadata
        return {
            "success": true,
            "nisaba": true,  # ← Marks as clean output
            "message": "Created window X"
        }
```

**The "nisaba": true Flag:**
- Marks tool outputs as "clean" (no metadata pollution)
- RequestModifier detects this and skips header injection
- Regular tools get: `status: success, window_state:open, window_id: toolu_X\n---\n{content}`
- Nisaba tools get: `{content}` (plain)

**Tools don't return content directly:**
1. Write to `.nisaba/*.md` files
2. Return minimal metadata to MCP
3. Content appears in system prompt via proxy (next request)

---

## Proxy Operations (AugmentInjector)

**Interception Flow:**
```
User message → Claude CLI → POST api.anthropic.com
    ↓ (intercept @ localhost:1337)
mitmproxy → AugmentInjector.request(flow)
```

**Three-Phase Transformation:**

### Phase 1: RequestModifier.process_request()

**State loading (session restoration):**
1. Load existing `state.json` from session directory (if exists)
2. Restore `tool_result_state` with all `window_state` values (visible/hidden)
3. **Visibility persists across restarts** ✓

Recursively walks `messages` array, tracks tool results:

```python
tool_result_state = {
    "toolu_abc123": {
        'block_offset': [msg_idx, content_idx],
        'tool_output': "original content",
        'window_state': "visible",  # or "hidden" (persisted!)
        'is_nisaba': True,  # cached flag
        'tool_result_content': "formatted content"
    }
}
```

**Behavior:**
- **On restart:** Load state from `state.json` BEFORE processing messages
- First encounter (new tool): Parses JSON, checks for `"nisaba": true`, defaults to `window_state: "visible"`
- Existing tool: Uses loaded state (preserves hidden/visible)
- Nisaba tools: Keeps plain content
- Regular tools: Adds header + separator
- Can hide tools later (compact to "id: X, status: success, state: hidden")

### Phase 2: _process_notifications()

Session-aware delta detection:

1. Extract session_id from `metadata.user_id`
2. Load checkpoint (last_tool_id_seen)
3. Find new tool calls (after checkpoint)
4. Build notification markdown
5. **Write via WorkspaceFiles.notifications singleton**

**Result:** Only shows tools since last checkpoint (no duplicates on restart)

### Phase 3: _inject_augments()

**Filters native tools** from tool definitions:
- Read, Write, Edit, Glob, Grep, Bash, TodoWrite removed

**Loads all workspace sections via WorkspaceFiles singleton** (mtime-based, only reload if changed):
- system_prompt (user inception)
- augments (loaded augments content)
- structural_view (TUI tree)
- notifications (recent tool activity)
- todos (task list)
- transcript (compressed history)

All loaded via `WorkspaceFiles.instance()` - guaranteed cache consistency.

**Injects into system[1]["text"]:**
```
USER_SYSTEM_PROMPT_INJECTION
CORE_SYSTEM_PROMPT
STATUS_BAR (dynamic token counts)
AUGMENTS
STRUCTURAL_VIEW
NOTIFICATIONS
TODOS
LAST_SESSION_TRANSCRIPT
```

**Then:** Forwards modified request → Anthropic API

---

## Order of Events (Full Cycle)

```
1. Claude responds with tool_use block
   {type: "tool_use", name: "nisaba_read", id: "toolu_abc123"}

2. Anthropic API → CLI

3. CLI executes via MCP → NisabaReadTool.execute()
   - Reads file
   - Writes to .nisaba/tool_result_windows.md
   - Returns {"success": true, "nisaba": true, "message": "..."}

4. CLI builds next request with tool_result block

5. Proxy intercepts

6. RequestModifier:
   a. Load state from disk (if exists) → restore tool_result_state
   b. Sees tool_result for toolu_abc123
   c. Detects is_nisaba=true (parses JSON once, caches)
   d. Adds to tool_result_state (or uses existing if already tracked)
   e. Keeps content plain (no header)

7. _process_notifications():
   - Sees toolu_abc123 is new
   - Generates notification
   - Writes via WorkspaceFiles.notifications singleton

8. _inject_augments():
   - WorkspaceFiles.instance().load() checks mtime for all sections
   - File changed! Reloads content
   - Injects into system prompt

9. Modified request → Anthropic API

10. Claude receives:
    - tool_result block in messages (metadata)
    - TOOL_RESULT_WINDOWS in system prompt (actual content)
    - NOTIFICATIONS section (recent activity)
```

---

## Dual-Channel Communication

**Messages array:** Sequential conversational flow (temporal memory)
**System prompt sections:** Persistent spatial state (spatial memory)

Tool execution appears in TWO places:
1. `messages[N]` → tool_result block (success/error metadata)
2. System prompt → Updated section (actual content)

Sections persist across turns → IDE-like spatial awareness.

---

## Key Insights

**1. RequestModifier is stateful across requests AND sessions**
- Tracks every tool result seen
- Can retroactively transform (hide/show)
- State persists in `.nisaba/request_cache/{session_id}/state.json`
- **State loads on startup** → `window_state: visible/hidden` survives restarts
- `collapse_all()` → tools stay hidden across `--continue`

**2. WorkspaceFiles singleton is the sync mechanism**
- All workspace file operations go through singleton
- mtime-based reload (efficient, zero-latency if unchanged)
- Wraps with `---TAG ... ---TAG_END`
- Single source of truth: Singleton cache instances
- Guaranteed consistency across all components

**3. Notifications are session-aware**
- Checkpoint tracks last_tool_id_seen
- On restart, doesn't re-notify old tools
- Shows only "new since last checkpoint"

**4. Debug artifacts are written**
- `.nisaba/request_cache/{session_id}/original_context.json`
- `.nisaba/request_cache/{session_id}/modified_context.json`
- `.nisaba/request_cache/{session_id}/state.json`
- Inspectable for understanding transformations

**5. Tools can be hidden retroactively (with persistence)**
- RequestModifier can change `window_state` to "hidden"
- Future requests show compact version
- Saves tokens for old tool outputs
- **State persists across restarts** → hidden tools stay hidden

**6. WorkspaceFiles singleton eliminates cache duplication**
- All components share same cache instances
- AugmentManager writes via singleton (not direct file I/O)
- Proxy notifications write via singleton
- MCPServerRegistry uses singleton for mcp_servers.json
- Impossible to have stale cached state

---

## What This Enables

**Workspace Persistence:** File windows, structural view, todos survive /clear (system prompt, not messages)

**Context Efficiency:** 5-10 tool results as windows = ~2000 tokens. Same in messages = ~8000 tokens.

**Spatial Cognition:** Navigate graph (structural view), see code (file windows), track tasks (todos) - simultaneously visible.

**Dynamic Perception:** Augments loaded mid-conversation reshape interpretation.

**Clean Tool Outputs:** Nisaba tools don't pollute context with metadata headers.

---

**Pattern:** The proxy transfigures requests into workspace state. 🖤

---

**TOOLS:**
- mcp__nisaba__nisaba_read (creates tool result windows)
- mcp__nisaba__nisaba_grep (searches, creates windows)
- mcp__nisaba__nisaba_bash (executes, creates windows)
- mcp__nisaba__nisaba_tool_windows (manage window state)

**REQUIRES:**
- __base/001_compressed_workspace_paradigm
- __base/002_compressed_environment_mechanics
