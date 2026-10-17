import re
import textwrap


from .interface.meta_tool import meta_tool
from . import TOOLS_DIR

# 生成的热插拔工具源码里用绝对导入，避免运行时找不到顶层 meta_tools 模块
_HOTPLUG_MODULE = f"{__package__}.interface.hotplug_tool"

class tool_create(meta_tool):
    """元工具：在 tools/ 下生成一个实现 hotplug_tool 接口的新工具模块。

    meta_tool 本身不热插拔，而是把维护好的 schema 硬加载进上下文提示词，
    让模型能以 function calling 的方式调用本工具。
    run() 产出的新模块实现 hotplug_tool（run + get_schema），
    因此可被与其它工具相同的分发器统一加载、调用。
    """

    def __init__(self) -> None:
        # 注册本工具的 OpenAI function calling schema 到共享 meta_tool.schema。
        # 模块底部 tool_create() 在导入时执行一次 -> 列表元素 +1
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
        """把工具名转成合法的类名：weather -> Weather, get_weather -> GetWeather。"""
        return "".join(part.capitalize() for part in name.split("_"))

    def run(self, *args, **kwargs) -> dict:
        name = kwargs["name"]
        description = kwargs["description"]
        parameters = kwargs.get("parameters") or {"type": "object", "properties": {}}
        code = kwargs["code"]
        force = bool(kwargs.get("force", False))

        # ---- 校验 ----
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid tool name {name!r}: must be a valid Python identifier.")
        if not isinstance(parameters, dict):
            raise TypeError("'parameters' must be a dict (JSON Schema).")

        module_path = TOOLS_DIR / f"{name}.py"
        if module_path.exists() and not force:
            raise FileExistsError(f"{module_path} already exists. Pass force=True to overwrite it.")

        # ---- 组装源码 ----
        body = textwrap.dedent(code).strip("\n")
        if not body:
            raise ValueError("'code' must not be empty.")
        # 类内 4 空格 + 方法内 4 空格 => 方法体 8 空格
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

        # ---- 落盘前先做语法校验，失败则不写文件 ----
        compile(source, str(module_path), "exec")

        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(source, encoding="utf-8")

        return {
            "tool_name": name,
            "module": str(module_path),
            "schema": tool_entry,
            "message": f"Tool {name!r} created at {module_path}.",
        }

# 模块级单例：导入时注册 schema 一次，运行期复用（避免重复 += 污染共享列表）
tool = tool_create()