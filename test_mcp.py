import asyncio
from sentinel_ai.integrations.mcp_adapter import MCPAdapter

async def main():
    print("Testing Atlassian MCP Client Initialization...")
    adapter = MCPAdapter()
    
    connected = await adapter.connect()
    if not connected:
        print("Failed to start Atlassian MCP stdio server.")
        return

    print("Success! Fetching available Atlassian tools...\n")
    tools_response = await adapter.execute("list_tools", {})
    
    if tools_response.get("status") == "success":
        tools = tools_response.get("tools", [])
        print(f"Discovered {len(tools)} Atlassian Tools:")
        for t in tools:
            print(f" - {t['name']}: {t['description'][:60]}...")
    else:
        print("Error fetching tools:", tools_response.get("error"))
        
    await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())
