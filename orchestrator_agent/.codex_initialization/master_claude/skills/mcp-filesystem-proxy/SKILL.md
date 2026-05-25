---
name: MCP Filesystem Proxy
description: Manages the MCP proxy server on port 8789 providing filesystem access to project directories. Use when user asks to start/stop the MCP server, enable filesystem access, or troubleshoot MCP connectivity issues.
allowed-tools: Bash, Read
---

# MCP Filesystem Proxy Server Management

## Overview

This skill manages an MCP (Model Context Protocol) proxy server that provides filesystem access to three key directories:
- `/home/adamsl/planner/nonprofit_finance_db/smart_menu`
- `/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools`
- `/home/adamsl/planner`

The proxy runs on `0.0.0.0:8789` and uses the `@modelcontextprotocol/server-filesystem` backend.

## Prerequisites

- Node.js and npx must be installed
- Wrapper script must exist at: `/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh`
- User must have permissions to bind to port 8789

## Instructions

### Starting the MCP Proxy Server

1. **Check if port 8789 is already in use**:
   ```bash
   lsof -i :8789
   ```

   - If output shows a process, note the PID
   - If no output, port is available - skip to step 3

2. **Handle existing process**:
   - Ask user: "Port 8789 is in use by process [PID]. Kill it and restart? (yes/no)"
   - If user confirms, kill the process:
     ```bash
     kill [PID]
     sleep 1
     ```
   - If user declines, abort and inform them the port is occupied

3. **Start the MCP proxy server in background**:
   ```bash
   cd /home/adamsl/planner && \
   npx -y mcp-proxy \
     --host 0.0.0.0 \
     --port 8789 \
     /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh &
   ```

4. **Wait for startup and verify**:
   ```bash
   sleep 2
   lsof -i :8789
   ```

   - If output shows node process listening, server started successfully
   - If no output, server failed to start - check error logs

5. **Report status to user**:
   ```
   ✅ MCP Filesystem Proxy Server is running

   Server Details:
   - URL: http://0.0.0.0:8789
   - PID: [process_id]
   - Exposed directories:
     • /home/adamsl/planner/nonprofit_finance_db/smart_menu
     • /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools
     • /home/adamsl/planner

   The server is running in the background.
   ```

### Stopping the MCP Proxy Server

1. **Find the running process**:
   ```bash
   lsof -i :8789
   ```

2. **Kill the process**:
   ```bash
   kill [PID]
   ```

3. **Verify shutdown**:
   ```bash
   sleep 1
   lsof -i :8789
   ```

   - If no output, server stopped successfully
   - If still running, use `kill -9 [PID]`

### Checking Server Status

1. **Check if process is listening**:
   ```bash
   lsof -i :8789
   ```

2. **Test server responsiveness** (optional):
   ```bash
   curl -s http://0.0.0.0:8789/ 2>&1 | head -5
   ```

3. **Report findings**:
   - If running: Show PID and confirm it's responding
   - If not running: Inform user server is stopped

### Troubleshooting

**Problem: Port already in use**
- Solution: Kill existing process or choose different port (requires modifying wrapper script)

**Problem: Server starts but immediately crashes**
- Check wrapper script exists and is executable:
  ```bash
  ls -la /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh
  ```
- Verify npx is available:
  ```bash
  which npx
  ```

**Problem: Permission denied on port 8789**
- Ports below 1024 require root, but 8789 should be accessible
- Check firewall rules: `sudo ufw status`

**Problem: Server running but not accessible**
- Verify binding to correct interface (0.0.0.0 vs 127.0.0.1)
- Check if firewall blocking the port

## Wrapper Script Reference

The wrapper script at `/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh` contains:

```bash
#!/bin/bash
cd /home/adamsl/planner
exec npx -y @modelcontextprotocol/server-filesystem \
  /home/adamsl/planner/nonprofit_finance_db/smart_menu \
  /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools \
  /home/adamsl/planner
```

This ensures the server runs from the correct working directory with access to all three specified directories.

## Examples

### Example 1: Starting the Server (Port Available)

**User request:**
```
Start the MCP filesystem proxy server
```

**You would:**

1. Check port status:
   ```bash
   lsof -i :8789
   ```
   Output: (empty - port available)

2. Start the server:
   ```bash
   cd /home/adamsl/planner && \
   npx -y mcp-proxy --host 0.0.0.0 --port 8789 \
     /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh &
   ```

3. Verify after 2 seconds:
   ```bash
   sleep 2
   lsof -i :8789
   ```
   Output: `node    12854 adamsl   22u  IPv4 3081771      0t0  TCP *:8789 (LISTEN)`

4. Report to user:
   ```
   ✅ MCP Filesystem Proxy Server is running

   Server Details:
   - URL: http://0.0.0.0:8789
   - PID: 12854
   - Exposed directories:
     • /home/adamsl/planner/nonprofit_finance_db/smart_menu
     • /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools
     • /home/adamsl/planner
   ```

### Example 2: Starting the Server (Port In Use)

**User request:**
```
Start the MCP server
```

**You would:**

1. Check port status:
   ```bash
   lsof -i :8789
   ```
   Output: `node    4140 adamsl   24u  IPv4 2908540      0t0  TCP *:8789 (LISTEN)`

2. Ask user:
   ```
   Port 8789 is already in use by process 4140 (node).
   Would you like me to kill it and restart the server? (yes/no)
   ```

3. If user responds "yes":
   ```bash
   kill 4140
   sleep 1
   ```

4. Verify process killed:
   ```bash
   lsof -i :8789
   ```
   Output: (empty)

5. Start the server:
   ```bash
   cd /home/adamsl/planner && \
   npx -y mcp-proxy --host 0.0.0.0 --port 8789 \
     /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh &
   ```

6. Verify and report as in Example 1

### Example 3: Checking Server Status

**User request:**
```
Is the MCP server running?
```

**You would:**

1. Check for listening process:
   ```bash
   lsof -i :8789
   ```
   Output: `node    12854 adamsl   22u  IPv4 3081771      0t0  TCP *:8789 (LISTEN)`

2. Report to user:
   ```
   ✅ Yes, the MCP Filesystem Proxy Server is running

   Status:
   - PID: 12854
   - Port: 8789
   - URL: http://0.0.0.0:8789
   ```

### Example 4: Stopping the Server

**User request:**
```
Stop the MCP proxy server
```

**You would:**

1. Find the process:
   ```bash
   lsof -i :8789
   ```
   Output: `node    12854 adamsl   22u  IPv4 3081771      0t0  TCP *:8789 (LISTEN)`

2. Kill the process:
   ```bash
   kill 12854
   sleep 1
   ```

3. Verify shutdown:
   ```bash
   lsof -i :8789
   ```
   Output: (empty)

4. Report to user:
   ```
   ✅ MCP Filesystem Proxy Server has been stopped

   Process 12854 terminated successfully.
   ```

### Example 5: Troubleshooting Failed Start

**User request:**
```
The MCP server won't start
```

**You would:**

1. Check if wrapper script exists:
   ```bash
   ls -la /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh
   ```
   Output: Shows file with execute permissions

2. Check if port is available:
   ```bash
   lsof -i :8789
   ```
   Output: (empty)

3. Check if npx is available:
   ```bash
   which npx
   ```
   Output: `/usr/local/bin/npx`

4. Try starting with error output:
   ```bash
   cd /home/adamsl/planner && \
   npx -y mcp-proxy --host 0.0.0.0 --port 8789 \
     /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh
   ```
   (Run in foreground to see errors)

5. Analyze error output and provide specific guidance based on the error message

## Related Files

- Wrapper script: `/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/start-mcp-fs.sh`
- Agent management docs: `/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/server_tools/agent_management/CLAUDE.md`

## Notes

- The server runs in the background and persists until manually stopped or system reboot
- Multiple clients can connect to the same proxy server
- The filesystem backend provides read/write access to the exposed directories
- Always verify server status after starting to ensure successful initialization
