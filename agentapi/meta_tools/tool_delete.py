import re

from . import TOOLS_DIR
from .interface.meta_tool import meta_tool


class tool_delete(meta_tool):

    def __init__(self) -> None:
        self.schema += [{
            "type": "function",
            "function": {
                "name": "tool_delete",
                "description": (
                    "Delete a hot-pluggable tool module under the tools/ directory by its "
                    "name. Note: if the tool was already loaded in the running session it "
                    "must be unregistered separately."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the tool to delete (also the module file name), e.g. 'weather'.",
                        },
                    },
                    "required": ["name"],
                },
            },
        }]

    def run(self, *args, **kwargs) -> dict:
        name = kwargs["name"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid tool name {name!r}: must be a valid Python identifier.")

        module_path = TOOLS_DIR / f"{name}.py"
        if not module_path.exists():
            raise FileNotFoundError(f"{module_path} does not exist.")

        module_path.unlink()
        for cached in (TOOLS_DIR / "__pycache__").glob(f"{name}*.pyc"):
            cached.unlink()

        return {
            "tool_name": name,
            "deleted": str(module_path),
            "message": f"Tool {name!r} deleted.",
        }

tool = tool_delete()