from pathlib import Path

from .interface.meta_tool import meta_tool

TOOLS_DIR = Path(__file__).parent.parent / "tools"

def get_schemas() -> list[dict]:
    meta_tool.schema.clear()
    from . import tool_create,tool_delete,tool_search,tool_view,tool_run,peer_agent,tool_skill
    return meta_tool.schema