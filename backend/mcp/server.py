from mcp.server.fastmcp import FastMCP
from backend.db.manager import DatabaseManager
from backend.llm.agent import NetworkAgent
from backend.llm.tools import ToolRegistry
from backend.deploy.pipeline import DeploymentPipeline
import logging

# Initialize FastMCP Server
mcp = FastMCP("NetAutoController")

# Global instances (initialized on startup if needed, or lazily)
db = DatabaseManager("net_topo.db")
pipeline = DeploymentPipeline(db)
agent = NetworkAgent(db)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back to the client. Useful for testing connection."""
    return f"Echo: {message}"

def run_mcp_server():
    """Entry point to run the MCP server."""
    # Initialize DB connection
    try:
        db.initialize()
        logger.info("Database initialized for MCP.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

    # Import and register resources/tools
    # We do this here to avoid circular imports if those modules import 'mcp' object
    from backend.mcp import resources, tools
    
    # Run the server
    mcp.run()

if __name__ == "__main__":
    run_mcp_server()
