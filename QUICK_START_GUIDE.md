# A2A Agent Communication - Quick Start Guide

## What Was Fixed

✅ **Startup Order Issue** - Created `start_all.sh` script that ensures:
1. WebSocket server starts first
2. Waits for port 3030 to be ready
3. Then starts orchestrator and other agents in order

✅ **Root Cause Documentation** - Created comprehensive breakdown in `COMMUNICATION_ISSUES_BREAKDOWN.md`

## Current Status (updated 2026-05-26)

### Working ✅
- WebSocket server running on port 3030
- All agents started via `bash start_all.sh`
- Jeri end-to-end: send message → task processed → JSON-RPC reply received
- `send_agent_message.py` rewritten as single async session (no stale-transport errors)
- Topic routing fixed: Jeri replies on `orchestrator`, watcher reads from there

### Previously Broken, Now Fixed ✅
- **Dual Transport Problem**: Resolved by `TransportManager` singleton in `transport_manager.py`
- **Stale asyncio loop**: `send_agent_message.py` rewritten — single `asyncio.run()` per invocation
- **Wrong WebSocket URL**: `.env` was pointing at Scissari Tailscale IP; corrected to `ws://127.0.0.1:3030`

## Quick Commands

### Start Everything
```bash
cd a2a_communicating_agents
bash start_all.sh
```

### Stop Everything
```bash
cd a2a_communicating_agents
bash stop_all.sh
```

### Test Jeri End-to-End
```bash
so
cd /home/adamsl
python3 a2a_communicating_agents/agent_messaging/send_agent_message.py jeri-agent "health check"
```

### Chat with Orchestrator
```bash
so
cd /home/adamsl/a2a-communicating-agents/agent_messaging
python3 orchestrator_chat.py
```

### Monitor Logs
```bash
# All logs
tail -f logs/*.log

# Just orchestrator
tail -f logs/orchestrator.log

# Just WebSocket
tail -f logs/websocket.log
```

## Architecture Issues Remaining

See `COMMUNICATION_ISSUES_BREAKDOWN.md` for detailed analysis. Key issues:

### Issue #1: ✅ FIXED - Startup Order
- **Was**: Orchestrator started before WebSocket server
- **Now**: WebSocket server starts first, orchestrator connects

### Issue #2: ⚠️ NEEDS FIX - Dual Transport Architecture
- **Problem**: `inbox()` function creates new transport instead of using shared WebSocket
- **Impact**: Orchestrator reads messages via RAG, not WebSocket
- **Solution**: Refactor to use Singleton transport or messenger instance

### Issue #3: ⚠️ NEEDS FIX - No Connection Sharing
- **Problem**: Every message operation creates new transport
- **Impact**: Sentence transformer loaded repeatedly, no connection reuse
- **Solution**: Implement TransportManager singleton

## Next Steps to Fully Fix Communication

### Option A: Quick Architectural Fix (2 hours)
Implement Singleton transport pattern:
```python
# Create TransportManager singleton
# Update inbox() and post_message() to use shared transport
# Refactor orchestrator to use messenger instance consistently
```

### Option B: Complete Async Refactor (1 day)
Convert to async/await with WebSocket subscriptions:
```python
# Convert orchestrator to async
# Use WebSocket subscribe/callback pattern
# Eliminate polling and RAG transport entirely
```

## Testing the Current System

Despite the dual-transport issue, you can still test communication:

1. **Start all agents**:
   ```bash
   bash start_all.sh
   ```

2. **Send a test message**:
   ```bash
   cd agent_messaging
   python3 orchestrator_chat.py
   # Type: please write a hello world snippet in web assembly
   ```

3. **Check what happens**:
   - orchestrator_chat sends via WebSocket ✅
   - Orchestrator reads via RAG (because of inbox() issue) ⚠️
   - Message might not be delivered properly

## Recommended Action

Choose one of these paths:

### Path 1: Live with Dual Transport (Current State)
- Pro: No code changes needed
- Con: Messages go through RAG, slower, inefficient
- Use: If you just need something working now

### Path 2: Fix the Architecture (Recommended)
- Pro: Proper WebSocket communication, fast, efficient
- Con: Requires refactoring messenger.py and orchestrator main.py
- Time: 2-3 hours
- See: `COMMUNICATION_ISSUES_BREAKDOWN.md` Solutions section

### Path 3: Complete Async Rewrite
- Pro: Modern, scalable, real-time
- Con: Significant refactoring
- Time: 1 day
- See: `COMMUNICATION_ISSUES_BREAKDOWN.md` Phase 3

## Questions?

See the detailed breakdown in `COMMUNICATION_ISSUES_BREAKDOWN.md` for:
- Root cause analysis with code examples
- GoF design patterns for solutions
- Implementation priority and steps
- Testing strategies
