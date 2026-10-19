import importlib.util
import re

from . import TOOLS_DIR
from .interface.hotplug_tool import hotplug_tool
from .interface.meta_tool import meta_tool


def _load_tool(name: str):
    module_path = TOOLS_DIR / f"{name}.py"
    if not module_path.exists():
        raise FileNotFoundError(f"工具 {name!r} 不存在，请先用 tool_search 查找可用工具")
    spec = importlib.util.spec_from_file_location(f"tools_{name}", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, hotplug_tool) and obj is not hotplug_tool:
            return obj()
    raise TypeError(f"No hotplug_tool subclass found in: {module_path}")


def _resolve_tool(name: str):
    try:
        module = importlib.import_module(f"{__package__}.{name}")
    except ModuleNotFoundError:
        module = None
    if module is not None:
        instance = getattr(module, "tool", None)
        if instance is not None and hasattr(instance, "run"):
            return instance
    return _load_tool(name)


class tool_run(meta_tool):

    def __init__(self) -> None:
        self.schema += [{
            "type": "function",
            "function": {
                "name": "tool_run",
                "description": (
                    "Run a tool by name. The target may be a meta tool (meta_tools) or "
                    "a hot-pluggable module under tools/. Arguments can be given either "
                    "as a keyword object ('arguments') or a positional array ('args'). "
                    "This meta tool will run any tools, can cause a disastrous consequences, KEEP SAFE. Make sure all the actions are rollbackable and remind user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Name of the tool to run, e.g. 'tool_search' or 'weather'.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Keyword arguments forwarded to the tool's run(**arguments).",
                        },
                        "args": {
                            "type": "array",
                            "description": "Positional arguments forwarded to the tool's run(*args).",
                        },
                    },
                    "required": ["tool_name"],
                },
            },
        }]

    def run(self, *args, **kwargs):
        if "tool_name" in kwargs:
            name = kwargs["tool_name"]
        elif args:
            name = args[0]
        else:
            raise ValueError("'tool_name' is required.")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid tool name {name!r}: must be a valid Python identifier.")

        target = _resolve_tool(name)

        if kwargs.get("arguments") is not None:
            if not isinstance(kwargs["arguments"], dict):
                raise TypeError("'arguments' must be an object.")
            return target.run(**kwargs["arguments"])

        extra = {k: v for k, v in kwargs.items() if k not in ("tool_name", "arguments", "args")}
        if extra:
            return target.run(**extra)

        if "args" in kwargs and kwargs["args"] is not None:
            positional = kwargs["args"]
        else:
            positional = list(args[1:])
        if not isinstance(positional, (list, tuple)):
            raise TypeError("'args' must be an array of positional arguments.")
        return target.run(*positional)


tool = tool_run()