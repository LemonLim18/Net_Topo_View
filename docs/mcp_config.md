# MCP Server Configuration Guide

To use the Network Controller tools with Claude Desktop, add the following configuration to your `claude_desktop_config.json`:

## Windows
Location: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "NetTopoController": {
      "command": "C:\\Users\\limmi\\OneDrive\\Desktop\\NetTopoView\\backend\\venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\limmi\\OneDrive\\Desktop\\NetTopoView\\backend\\mcp\\server.py"
      ]
    }
  }
}
```

## MacOS / Linux
Location: `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or `~/.config/Claude/claude_desktop_config.json` (Linux)

```json
{
  "mcpServers": {
    "NetTopoController": {
      "command": "/absolute/path/to/backend/venv/bin/python",
      "args": [
        "/absolute/path/to/backend/mcp/server.py"
      ]
    }
  }
}
```

## Verification
1.  Restart Claude Desktop.
2.  Look for the 🔌 icon in the top right.
3.  You should see "NetTopoController" connected.
4.  Try asking: "Can you list the devices in my network?"
