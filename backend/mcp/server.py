from mcp.server.fastmcp import FastMCP
import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:
    from db.manager import DatabaseManager
    from llm.agent import NetworkAgent
    from llm.tools import ToolRegistry
    from deploy.pipeline import DeploymentPipeline
except ImportError:  # pragma: no cover - package import path
    from ..db.manager import DatabaseManager
    from ..llm.agent import NetworkAgent
    from ..llm.tools import ToolRegistry
    from ..deploy.pipeline import DeploymentPipeline

# Initialize FastMCP Server
mcp = FastMCP("NetAutoController")

_DB_PATH = Path(__file__).resolve().parent.parent / "db" / "netauto.db"

# Global instances (initialized on startup if needed, or lazily)
db = DatabaseManager(str(_DB_PATH))
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
    if __package__:
        from . import resources, tools
    else:  # pragma: no cover - direct script execution path
        _load_local_modules_for_script()
    
    # Run the server
    mcp.run()


def _load_local_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_local_modules_for_script() -> None:
    previous_server = sys.modules.get("mcp.server")
    binding_module = ModuleType("mcp.server")
    binding_module.mcp = mcp
    binding_module.db = db
    binding_module.agent = agent
    binding_module.pipeline = pipeline
    sys.modules["mcp.server"] = binding_module
    try:
        _load_local_module("nettopoview_mcp_resources", Path(__file__).with_name("resources.py"))
        _load_local_module("nettopoview_mcp_tools", Path(__file__).with_name("tools.py"))
    finally:
        if previous_server is not None:
            sys.modules["mcp.server"] = previous_server
        else:
            sys.modules.pop("mcp.server", None)

if __name__ == "__main__":
    run_mcp_server()
