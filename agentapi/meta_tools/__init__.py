from pathlib import Path

from .interface.meta_tool import meta_tool

TOOLS_DIR = Path(__file__).parent.parent / "tools"

def get_schemas() -> list[dict]:
    """返回全部元工具的 OpenAI function calling schema（现成列表，直接给 chat_api 用）。"""
    from . import tool_create,tool_delete,tool_search,tool_view,tool_run,peer_agent
    return meta_tool.schema