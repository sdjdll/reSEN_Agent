import re

from . import TOOLS_DIR
from .interface.meta_tool import meta_tool

class tool_view(meta_tool):
    """元工具：查看 tools/ 下某个工具的定义（schema 与源码）。"""

    def __init__(self) -> None:
        self.schema += [{
            "type": "function",
            "function": {
                "name": "tool_view",
                "description": (
                    "Inspect an existing hot-pluggable tool module under the tools/ directory "
                    "by name. Returns its function schema and Python source code."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the tool to inspect, e.g. 'weather'.",
                        },
                    },
                    "required": ["name"],
                },
            },
        }]

    def load_module(self, name: str, module_path):
        """按文件路径加载工具模块，返回模块对象。"""
        import importlib.util
        import sys

        module_name = f"blackplum_tool_{name}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load tool module {name!r} from {module_path}.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def run(self, *args, **kwargs) -> dict:
        name = kwargs["name"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid tool name {name!r}: must be a valid Python identifier.")

        module_path = TOOLS_DIR / f"{name}.py"
        if not module_path.exists():
            raise FileNotFoundError(f"{module_path} does not exist.")

        source = module_path.read_text(encoding="utf-8")
        module = self.load_module(name, module_path)
        schema = getattr(module, "schema", None)
        if schema is None and hasattr(module, "tool"):
            schema = getattr(module.tool, "schema", None)
        return {"name": name, "schema": schema, "source": source}

# 模块级单例：导入时注册 schema 一次，运行期复用
tool = tool_view()