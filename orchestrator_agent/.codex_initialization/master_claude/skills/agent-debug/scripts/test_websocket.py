#!/usr/bin/env python3
"""
Test WebSocket server connectivity.
"""
import asyncio
import sys
from pathlib import Path

# Add project root's parent so a2a_communicating_agents is importable
PROJECT_ROOT = Path(__file__).resolve().parents[6]  # /home/adamsl/a2a_communicating_agents
sys.path.insert(0, str(PROJECT_ROOT.parent))  # for: from a2a_communicating_agents.X import Y
sys.path.insert(0, str(PROJECT_ROOT))          # for: from rag_system.X import Y

from a2a_communicating_agents.agent_messaging.websocket_transport import WebSocketTransport
from a2a_communicating_agents.agent_messaging.message_models import ConnectionConfig

async def test_connection():
    """Test WebSocket server connection."""
    print("🔌 Testing WebSocket connection to ws://localhost:3030...")

    config = ConnectionConfig(url="ws://localhost:3030")
    transport = WebSocketTransport(config)
    transport.agent_id = "test-connection-agent"

    try:
        await transport.connect()
        print("✅ Successfully connected to WebSocket server!")
        print(f"   Agent ID: {transport.agent_id}")
        await transport.disconnect()
        print("✅ Successfully disconnected")
        return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_connection())
    sys.exit(0 if result else 1)
