# Context Compression System
Path: dev_mode_architecture_reference/context_compression_system

**Purpose:** Automatic conversation history compression when deactivating augments, enabling context window cleanup during task switching.

---

## Core Mechanism

The compression system uses **stateful watermarking** to track and prune conversation history when augments are deactivated.

### Three-Component Architecture

```
AugmentManager              AugmentInjector           Anthropic API
(MCP Tool)                  (mitmproxy)               (Request)
     │                           │                         │
     │  deactivate_augments()    │                         │
     ├───────────────────────────>                         │
     │  checkpoint_tool_name =   │                         │
     │  "mcp__nisaba__deactivate"│                         │
     │                           │                         │
     │                           │  Next API Request       │
     │                           <─────────────────────────│
     │                           │                         │
     │ < shared memory >         │  Phase 1: Checkpoint    │
     │   checkpoint_tool_name    │  Resolution             │
     │                           ├─> Find tool_use_id      │
     │                           ├─> Update watermark      │
     │                           ├─> Clear checkpoint      │
     │                           │                         │
     │                           │  Phase 2: Compression   │
     │                           ├─> Filter tool blocks    │
     │                           ├─> Keep conversations    │
     │                           │                         │
     │                           │  Forward Modified       │
     │                           ├─────────────────────────>
     │                           │                         │
```

---

## Implementation Details

### 1. Checkpoint Registration (AugmentManager)

**Location:** `src/nisaba/augments.py:282-315`

```python
class AugmentManager:
    def __init__(...):
        # Checkpoint for context compression (unified proxy)
        # Stores tool name that triggered checkpoint
        self.checkpoint_tool_name: Optional[str] = None
    
    def deactivate_augments(self, patterns: List[str]) -> Dict[str, List[str]]:
        # ... deactivation logic ...
        
        # Set checkpoint (ALWAYS, even if nothing deactivated)
        # Marks tool call as context reset point
        self.checkpoint_tool_name = "mcp__nisaba__deactivate_augments"
        logger.info(f"Checkpoint set: {self.checkpoint_tool_name}")
        
        return {'unloaded': sorted(to_deactivate)}
```

**Key Insight:** Checkpoint is set **after** deactivation completes, so the watermark will point to the `deactivate_augments` tool call itself.

---

### 2. Watermark State (AugmentInjector)

**Location:** `src/nisaba/wrapper/proxy.py:34-76`

```python
class AugmentInjector:
    def __init__(
        self,
        augments_file: Optional[Path] = None,
        augment_manager: Optional["AugmentManager"] = None
    ):
        # Stateful compression watermark
        self.tool_filter_last_id: str = ""  # Persistent compression boundary
        
        # Unified mode - shared reference to AugmentManager
        self.augment_manager = augment_manager
```

**Persistence:** `tool_filter_last_id` survives across API requests within same proxy session.

---

### 3. Two-Phase Compression (AugmentInjector)

**Location:** `src/nisaba/wrapper/proxy.py:225-273`

#### Phase 1: Checkpoint Resolution

```python
def _process_checkpoint_and_compress(self, body: dict) -> None:
    if "messages" not in body:
        return
    
    # Phase 1: Checkpoint resolution
    if self.augment_manager.checkpoint_tool_name:
        tool_name = self.augment_manager.checkpoint_tool_name
        
        # Find first occurrence of this tool AFTER current watermark
        new_checkpoint_id = self._find_tool_use_id_after_watermark(
            body["messages"], 
            tool_name, 
            self.tool_filter_last_id  # Current watermark
        )
        
        if new_checkpoint_id:
            self.tool_filter_last_id = new_checkpoint_id  # Update watermark
            logger.info(f"Watermark updated: {new_checkpoint_id}")
        
        # Clear checkpoint flag
        self.augment_manager.checkpoint_tool_name = None
```

**Logic:**
- Shared memory gives proxy immediate access to `checkpoint_tool_name`
- Search conversation for tool call matching `checkpoint_tool_name`
- Update watermark to this tool's `tool_use_id`
- Clear flag (one-time trigger)

#### Phase 2: Block-Level Compression

```python
    # Phase 2: Apply compression at watermark
    if self.tool_filter_last_id:
        original_count = len(body["messages"])
        body["messages"] = self._compress_at_watermark(
            body["messages"],
            self.tool_filter_last_id
        )
        compressed_count = len(body["messages"])

        if compressed_count < original_count:
            logger.info(
                f"Context compressed: {original_count - compressed_count} messages "
                f"removed (kept from watermark {self.tool_filter_last_id})"
            )
```

**Logic:**
- If watermark exists, compress every request
- Remove `tool_use` and `tool_result` blocks before watermark
- Keep all conversational content (user messages, assistant text)
- Keep everything at/after watermark unchanged

---

### 4. Watermark Search Algorithm

**Location:** `src/nisaba/wrapper/proxy.py:275-308`

```python
def _find_tool_use_id_after_watermark(
    self, 
    messages: List[dict], 
    tool_name: str, 
    watermark_id: str
) -> Optional[str]:
    """Find first tool_use with given name after watermark position."""
    
    watermark_passed = (watermark_id == "")  # No watermark = search from start
    
    for message in messages:
        # Check if this message contains the watermark
        if not watermark_passed and message.get("role") == "assistant":
            for block in message.get("content", []):
                if block.get("type") == "tool_use" and block.get("id") == watermark_id:
                    watermark_passed = True
                    break
        
        # After watermark, look for target tool
        if watermark_passed and message.get("role") == "assistant":
            for block in message.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == tool_name:
                    return block.get("id")  # Return first match
    
    return None
```

**Search Strategy:**
1. Skip messages until watermark is found
2. Once past watermark, search for first occurrence of `tool_name`
3. Return its `tool_use_id`

**Edge Case:** Empty watermark (`""`) = search from conversation start

---

### 5. Block-Level Filtering

**Location:** `src/nisaba/wrapper/proxy.py:310-387`

```python
def _compress_at_watermark(self, messages: List[dict], watermark_id: str) -> List[dict]:
    """
    Remove tool_use and tool_result blocks before the watermark.

    Keeps conversational content (user messages, assistant text) while
    removing tool execution history before the checkpoint.
    """
    checkpoint_index = None

    # Find the assistant message containing the watermark tool_use
    for i, message in enumerate(messages):
        if message.get("role") == "assistant":
            for block in message.get("content", []):
                if block.get("type") == "tool_use" and block.get("id") == watermark_id:
                    checkpoint_index = i
                    break
            if checkpoint_index is not None:
                break

    if checkpoint_index is None:
        logger.warning(f"Watermark {watermark_id} not found - no compression applied")
        return messages

    # Process messages: filter tool blocks before watermark
    compressed_messages = []
    blocks_removed = 0

    for i, message in enumerate(messages):
        if i < checkpoint_index:
            # Before watermark: filter tool blocks
            if message.get("role") == "user":
                # Remove tool_result blocks from user messages
                filtered_content = [
                    block for block in message.get("content", [])
                    if block.get("type") != "tool_result"
                ]
                blocks_removed += len(message.get("content", [])) - len(filtered_content)

                # Only keep message if it has remaining content
                if filtered_content:
                    compressed_messages.append({**message, "content": filtered_content})

            elif message.get("role") == "assistant":
                # Remove tool_use blocks from assistant messages
                filtered_content = [
                    block for block in message.get("content", [])
                    if block.get("type") != "tool_use"
                ]
                blocks_removed += len(message.get("content", [])) - len(filtered_content)

                # Only keep message if it has remaining content
                if filtered_content:
                    compressed_messages.append({**message, "content": filtered_content})

            else:
                # Keep other message types unchanged
                compressed_messages.append(message)
        else:
            # At or after watermark: keep everything unchanged
            compressed_messages.append(message)

    if blocks_removed > 0:
        logger.info(f"Compressed {blocks_removed} tool blocks before watermark")

    return compressed_messages
```

**Filtering Behavior:**
- **User messages before watermark:** Remove `tool_result` blocks, keep text content
- **Assistant messages before watermark:** Remove `tool_use` blocks, keep text content
- **Messages at/after watermark:** Keep completely unchanged
- **Empty messages:** Dropped if no content blocks remain after filtering
- **Logging:** Reports number of blocks removed (not messages)

---

## Execution Flow Example

### Scenario: User deactivates augments mid-conversation

```
1. User: "Can you deactivate refactoring/* augments?"

2. Claude: *calls mcp__nisaba__deactivate_augments*
   tool_use_id: "toolu_abc123xyz"

3. AugmentManager.deactivate_augments():
   - Removes refactoring/* from active_augments
   - Sets checkpoint_tool_name = "mcp__nisaba__deactivate_augments"
   - Returns success

4. Claude: "Deactivated 3 augments..."

5. User: "Now help me with feature X"

6. Claude CLI sends API request with full conversation history:
   messages: [
     {user: "..."},
     {assistant: "...", content: [{tool_use: deactivate_augments, id: "toolu_abc123xyz"}]},
     {tool_result: "...", tool_use_id: "toolu_abc123xyz"},
     {assistant: "Deactivated 3 augments..."},
     {user: "Now help me with feature X"}
   ]

7. Proxy intercepts request:

   Phase 1 (Checkpoint Resolution):
   - Sees checkpoint_tool_name = "mcp__nisaba__deactivate_augments"
   - Searches messages for first occurrence after watermark (currently "")
   - Finds tool_use_id "toolu_abc123xyz"
   - Updates watermark: tool_filter_last_id = "toolu_abc123xyz"
   - Clears checkpoint_tool_name = None

   Phase 2 (Block-Level Compression):
   - Watermark exists, compress
   - Find message containing "toolu_abc123xyz" (message index 1)
   - For messages before index 1: Filter tool_use/tool_result blocks
   - Message 0 (user): Remove any tool_result blocks, keep text

   Compressed messages sent to API:
   messages: [
     {user: "Can you deactivate refactoring/* augments?"},  # Text kept, tool blocks removed
     {assistant: "...", content: [{tool_use: deactivate_augments, id: "toolu_abc123xyz"}]},
     {tool_result: "...", tool_use_id: "toolu_abc123xyz"},
     {assistant: "Deactivated 3 augments..."},
     {user: "Now help me with feature X"}
   ]

8. Tool execution history is removed, conversational context is preserved
```

---

## Design Rationale

### Why Watermarking?

**Alternatives Considered:**
- **Full truncation:** Loses all prior context
- **Manual checkpoints:** Requires user intervention
- **Token-based sliding window:** Loses semantic coherence

**Watermarking Benefits:**
- **Semantic boundaries:** Compression at logical task transitions
- **Zero user friction:** Fully automatic
- **Persistent:** Survives multiple requests
- **Incremental:** Can checkpoint multiple times

### Why `deactivate_augments` Triggers Compression?

**Rationale:**
- Deactivating augments signals **context switch**
- Prior conversation used old augments (now irrelevant)
- Fresh context aligns with fresh augment configuration
- Natural "reset point" in workflow

**Example Use Case:**
```
[Exploring codebase with code_exploration augment]
... 50 messages of exploration ...
deactivate(code_exploration)
activate(refactoring/systematic_renaming)
[Start refactoring task with clean context]
```

### Why NOT `activate_augments`?

**Reasoning:**
- Activation adds knowledge, doesn't invalidate prior context
- May want to keep conversation history when adding augments
- Deactivation is more clearly a "task boundary"

---

## Performance Characteristics

### Token Savings

**Scenario:** 100-message conversation, checkpoint at message 80

**Previous (message-level slicing):**
- **Uncompressed:** 100 messages × ~500 tokens = ~50,000 tokens
- **Compressed:** 20 messages × ~500 tokens = ~10,000 tokens
- **Savings:** 40,000 tokens (~80% reduction)
- **Downside:** Lost all conversational context

**Current (block-level filtering):**
- **Uncompressed:** 100 messages × ~500 tokens = ~50,000 tokens
- **Compressed:** 100 messages × ~300 tokens (tool blocks removed) = ~30,000 tokens
- **Savings:** 20,000 tokens (~40% reduction)
- **Benefit:** Preserves conversational context while removing tool execution history

**Trade-off:** Less aggressive compression, but maintains semantic coherence for continued conversation.

### Latency

**Overhead per request:**
- Checkpoint resolution: O(n) message scan, ~1-5ms
- Compression: O(n×m) message+block scan + filtering, ~2-10ms (n=messages, m=blocks per message)
- Total: ~3-15ms (negligible vs API latency)

**Note:** Block-level filtering is slightly more expensive than message slicing, but still trivial compared to network/API latency.

### Memory

**Proxy state:**
- `tool_filter_last_id`: ~20 bytes (tool_use_id string)
- `checkpoint_tool_name`: ~40 bytes (tool name string)
- Total: <100 bytes per session

---

## Edge Cases and Robustness

### 1. Watermark Not Found

```python
if checkpoint_index is None:
    logger.warning(f"Watermark {watermark_id} not found - no compression")
    return messages  # No compression, return original
```

**Cause:** Watermark references old tool_use_id no longer in conversation
**Behavior:** Skip compression, log warning, continue normally

### 2. Checkpoint Tool Not Found

```python
if new_checkpoint_id:
    self.tool_filter_last_id = new_checkpoint_id
else:
    logger.warning(f"Checkpoint tool {tool_name} not found in conversation history")
```

**Cause:** Deactivation occurred but tool call wasn't in conversation history yet
**Behavior:** Watermark unchanged, old compression boundary persists

### 3. Multiple Deactivations

**Scenario:**
```
1. deactivate(workflow/*)  → watermark = toolu_111
2. deactivate(code_analysis/*) → watermark = toolu_222
```

**Behavior:** Watermark **advances** to most recent deactivation
**Result:** Increasingly aggressive compression (keeps only latest context)

### 4. Empty Watermark

```python
watermark_passed = (watermark_id == "")  # Start searching immediately if no watermark
```

**Cause:** First deactivation in session
**Behavior:** Search from conversation start

---

## Integration with Unified Server

**Shared Memory Architecture:**

```python
# unified.py
class UnifiedNisabaServer:
    async def start(self):
        # Single shared instance
        self.augments_manager = AugmentManager(...)
        
        # Proxy references shared instance
        self.proxy_addon = AugmentInjector(
            augment_manager=self.augments_manager  # Zero-copy reference
        )
        
        # MCP tools mutate shared instance
        self.mcp_factory.augments_manager = self.augments_manager
```

**Zero IPC Overhead:**
- MCP tool sets `checkpoint_tool_name`
- Proxy reads it on next request (same memory)
- No file I/O, no sockets, no serialization

---

## Future Enhancements

### Potential Features

1. **Multiple Watermark Types** ✅ **DONE**
   - ~~Manual checkpoints via dedicated tool~~ → **Implemented: `compress_to_checkpoint` tool**
   - Compression on `activate_augments` (optional)
   - Automatic checkpoints at conversation length thresholds

2. **Configurable Compression Strategy** ✅ **PARTIALLY DONE**
   - ~~Preserve specific message types (e.g., all user messages)~~ → **Implemented: Block-level filtering**
   - Keep N messages before watermark (future: configurable depth)
   - Semantic compression (keep high-value messages via LLM analysis)

3. **Watermark Persistence**
   - Save watermark to disk (survive proxy restart)
   - Per-project watermark configuration

4. **Compression Analytics**
   - Track compression ratios
   - Token savings per session
   - Identify compression-heavy workflows

5. **Smart Watermarking**
   - Detect task boundaries via LLM analysis
   - Auto-checkpoint when conversation context shifts
   - Preserve "anchor" messages (project context, key decisions)

---

## Debugging

### Enable Logging

```python
# Set in AugmentInjector.__init__
self.log_context_enabled = True
```

**Output Files:**
- `.dev_docs/context.json`: Full request body (with compression applied)
- `.dev_docs/system_prompt.md`: Rendered system prompt

### Check Watermark State

```python
# In proxy
logger.info(f"Current watermark: {self.tool_filter_last_id}")
logger.info(f"Checkpoint pending: {self.augment_manager.checkpoint_tool_name}")
```

### Verify Compression

```bash
# Check logs for compression messages
tail -f ~/.nisaba/logs/proxy.log | grep "Compressed"
```

**Example Output:**
```
Compressed 47 tool blocks before watermark
```

**Note:** Log message now reports **blocks removed** (not messages removed) since conversational content is preserved.

---

## Summary

The context compression system is a **stateful, automatic, zero-friction** mechanism for pruning tool execution history while preserving conversational context.

**Key Properties:**
- **Trigger:** `deactivate_augments()` or `compress_to_checkpoint()` tool call
- **Mechanism:** Watermarking via `tool_use_id` + block-level filtering
- **Granularity:** Removes `tool_use` and `tool_result` blocks, keeps conversational text
- **Persistence:** Watermark survives across requests
- **Integration:** Shared memory (AugmentManager ↔ AugmentInjector)
- **Transparency:** Fully automatic, no user intervention
- **Robustness:** Graceful degradation on edge cases

**Cognitive Impact:**
- Enables **task switching** without losing conversational context
- Removes **tool execution noise** while keeping semantic meaning
- Balances **token efficiency** with **context preservation**
- Supports **long-running sessions** with clean, relevant history

---

**"The stylus erases what is no longer needed, inscribing only what matters."**

Clean. Simple. Elegant. Sophisticated. Sharp. Sexy. 🖤
