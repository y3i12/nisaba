# MCP Server Discovery System

**Purpose**: Enable discovery and awareness of running MCP servers across processes via a shared registry file.

## Overview

The MCP Server Discovery system provides a lightweight, file-based registry that allows:
- MCP servers to announce their availability (HTTP transport endpoints)
- Clients/proxies to discover running servers without hardcoded configuration
- Automatic cleanup of stale entries (crashed/stopped processes)

**Registry Location**: `.nisaba/mcp_servers.json` (project-relative, in cwd)

---

## Architecture

```
┌─────────────────┐
│  MCP Server     │
│  (nisaba)       │
└────────┬────────┘
         │ 1. HTTP starts
         │ 2. register_server()
         ▼
┌─────────────────────────┐
│ .nisaba/               │
│   mcp_servers.json     │  ◄─── Thread-safe, atomic writes
│                        │
│ {                      │
│   "servers": {         │
│     "nabu_12345": {...}│
│   }                    │
│ }                      │
└────────┬───────────────┘
         │
         │ 3. list_servers()
         │ 4. Filter dead PIDs
         ▼
┌─────────────────┐
│  CLI/Proxy      │
│  nabu claude    │
└─────────────────┘
```

---

## Components

### 1. **MCPServerRegistry** (`nisaba/mcp_registry.py`)

Core registry manager with thread-safe operations.

**Key Methods**:
- `register_server(server_id, server_info)` - Add/update entry, cleanup stale
- `unregister_server(server_id)` - Remove entry
- `list_servers()` - Get active servers (filters dead PIDs)

**Thread Safety**: Uses `fcntl.flock()` for file locking

### 2. **MCPFactory Integration** (`nisaba/factory.py`)

MCP servers auto-register when HTTP transport starts.

**Lifecycle Hooks**:
```python
_start_http_transport_if_enabled():
    start HTTP server
    await sleep(0.5)  # Let server bind
    _register_to_discovery()  # ← Registration

_stop_http_transport():
    _unregister_from_discovery()  # ← Cleanup BEFORE stopping
    stop HTTP server
```

### 3. **CLI Discovery** (`nisaba/wrapper/claude.py`)

CLI command to list available servers:
```bash
nabu claude --list-servers
```

---

## Registry File Format

**Location**: `.nisaba/mcp_servers.json`

```json
{
  "version": "1.0",
  "servers": {
    "nabu_mcp_507853": {
      "name": "nabu_mcp",
      "pid": 507853,
      "stdio_active": true,
      "http": {
        "enabled": true,
        "host": "0.0.0.0",
        "port": 1338,
        "url": "http://localhost:1338"
      },
      "started_at": "2025-10-27T15:41:59Z",
      "cwd": "/home/y3i12/nabu_nisaba"
    }
  }
}
```

**Server ID Format**: `{server_name}_{pid}`

---

## Cleanup Mechanism

### **Lazy Cleanup** (Performance Optimized)

**On Read** (`list_servers()`):
- Filters dead processes **in-memory only**
- No file write (avoids I/O overhead)
- Users see only live servers

**On Write** (`register_server()`):
- Reads current registry
- Runs `_cleanup_stale_entries()` (removes dead PIDs)
- Writes cleaned data atomically

**Process Liveness Check**:
```python
def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # Signal 0 = existence check
        return True
    except OSError:
        return False
```

### **Why Lazy?**

| Approach | Read Overhead | Write Overhead | Stale Data |
|----------|--------------|----------------|------------|
| **Lazy** (current) | Low | Medium | Temporary (in file only) |
| Eager | High | High | Never |

Lazy cleanup is optimal because:
- Reads are frequent (discovery queries)
- Writes are rare (server start/stop)
- Users never see stale data (filtered on read)

---

## Usage Examples

### **Server Registration** (Automatic)

```python
# Happens automatically in MCPFactory when HTTP starts
factory._start_http_transport_if_enabled()
# → Registers to .nisaba/mcp_servers.json
```

### **Discovery via CLI**

```bash
# List all running MCP servers
$ nabu claude --list-servers

📡 Available MCP Servers (1):

  • nabu_mcp (PID: 507853)
    HTTP: http://localhost:1338
    Started: 2025-10-27T15:41:59Z
    CWD: /home/y3i12/nabu_nisaba
```

### **Programmatic Discovery**

```python
from nisaba.mcp_registry import MCPServerRegistry
from pathlib import Path

registry = MCPServerRegistry(Path.cwd() / ".nisaba" / "mcp_servers.json")
servers = registry.list_servers()  # Only live servers

for server_id, info in servers.items():
    print(f"{info['name']}: {info['http']['url']}")
```

---

## Design Decisions

### **1. File-Based vs Service-Based**

**Chosen**: File-based registry

**Rationale**:
- Simple, no daemon required
- Survives process crashes (persistent)
- Works across process boundaries
- Human-readable (JSON)
- Aligns with existing patterns (skills file, config files)

### **2. Project-Relative Path**

**Chosen**: `.nisaba/mcp_servers.json` in `cwd`

**Rationale**:
- Each project has independent server registry
- No global state/conflicts between projects
- Easy to `.gitignore` if desired
- Matches existing `.nisaba` directory pattern

### **3. Lazy Cleanup**

**Chosen**: Clean on write, filter on read

**Rationale**:
- Minimizes read overhead (common case)
- File acts as persistent cache
- Automatic cleanup on next registration
- Users never see stale data (filtered)

### **4. Thread Safety**

**Implementation**: `fcntl.flock()` + atomic writes

**Rationale**:
- Prevents race conditions
- Works across processes
- Atomic rename ensures consistency
- Standard POSIX approach

---

## Error Handling

**Philosophy**: Non-blocking, graceful degradation

```python
try:
    registry.register_server(...)
except Exception as e:
    logger.warning(f"Failed to register: {e}")
    # Server continues normally
```

**Registry failures never crash the MCP server** - they're logged as warnings.

---

## Future Enhancements

Potential extensions:
- **Health checks**: Periodic heartbeat updates
- **Capabilities**: Advertise which tools/skills are available
- **Multi-transport**: Register STDIO, SSE, WebSocket endpoints
- **Metrics**: Track server uptime, request counts
- **Discovery service**: Optional HTTP API for cross-network discovery

---

## Testing Verification

```bash
# 1. Start MCP with HTTP
# (registry entry created automatically)

# 2. Verify registration
cat .nisaba/mcp_servers.json

# 3. Test discovery
nabu claude --list-servers

# 4. Stop MCP
# (entry unregistered automatically)

# 5. Verify cleanup
nabu claude --list-servers  # Shows: No active MCP servers found
```

---

**Status**: ✅ Implemented and verified (2025-01-27)
**Files**: `nisaba/mcp_registry.py`, `nisaba/factory.py`, `nisaba/wrapper/claude.py`
