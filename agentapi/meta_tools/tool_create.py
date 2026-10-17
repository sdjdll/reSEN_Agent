import re
import textwrap


from .interface.meta_tool import meta_tool
from . import TOOLS_DIR

_HOTPLUG_MODULE = f"{__package__}.interface.hotplug_tool"

class tool_create(meta_tool):

    def __init__(self) -> None:
        self.schema += [{
            "type": "function",
            "function": {
                "name": "tool_create",
                "description": (
                    "Create a new hot-pluggable tool module under the tools/ directory "
                    "from a name, a description, an argument JSON Schema and a Python "
                    "implementation body. Returns the created module path and its "
                    "function schema for later unified dispatch."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Tool name (also the module file name). Must be a valid Python identifier, e.g. 'weather'.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Human readable description of what the tool does.",
                        },
                        "parameters": {
                            "type": "object",
                            "description": "OpenAI function-calling argument JSON Schema (object with type/properties/required).",
                        },
                        "code": {
                            "type": "string",
                            "description": "Python statements implementing the body of the tool's run(self, *args, **kwargs).",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Overwrite the module if it already exists. Default false.",
                        },
                    },
                    "required": ["name", "description", "parameters", "code"],
                },
            },
        }]

    @staticmethod
    def _class_name(name: str) -> str:
        return "".join(part.capitalize() for part in name.split("_"))

    def run(self, *args, **kwargs) -> dict:
        name = kwargs["name"]
        description = kwargs["description"]
        parameters = kwargs.get("parameters") or {"type": "object", "properties": {}}
        code = kwargs["code"]
        force = bool(kwargs.get("force", False))

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid tool name {name!r}: must be a valid Python identifier.")
        if not isinstance(parameters, dict):
            raise TypeError("'parameters' must be a dict (JSON Schema).")

        module_path = TOOLS_DIR / f"{name}.py"
        if module_path.exists() and not force:
            raise FileExistsError(f"{module_path} already exists. Pass force=True to overwrite it.")

        body = textwrap.dedent(code).strip("\n")
        if not body:
            raise ValueError("'code' must not be empty.")
        indented = textwrap.indent(body, " " * 8)

        tool_entry = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

        source = (
            f"from {_HOTPLUG_MODULE} import hotplug_tool\n"
            "\n"
            "\n"
            f"class {self._class_name(name)}(hotplug_tool):\n"
            "    def get_schema(self) -> dict:\n"
            f"        return {tool_entry!r}\n"
            "\n"
            "    def run(self, *args, **kwargs):\n"
            f"{indented}\n"
        )

        compile(source, str(module_path), "exec")

        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(source, encoding="utf-8")

        return {
            "tool_name": name,
            "module": str(module_path),
            "schema": tool_entry,
            "message": f"Tool {name!r} created at {module_path}.",
        }

tool = tool_create()