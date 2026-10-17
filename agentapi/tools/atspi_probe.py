from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class AtspiProbe(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'atspi_probe', 'description': '用系统 Python(含 gi/Atspi) 运行一段 AT-SPI 探测脚本，返回标准输出（用于读取 Linux 桌面应用的可访问性元素树）。', 'parameters': {'properties': {'code': {'description': '要运行的 Python 脚本源码（用 /usr/bin/python3 执行）', 'type': 'string'}}, 'required': ['code'], 'type': 'object'}}}

    def run(self, *args, **kwargs):
        import json, subprocess, textwrap, os

        script = kwargs.get("code") or (args[0] if args else "")
        path = "/tmp/atspi_probe.py"
        with open(path, "w") as f:
            f.write(script)
        try:
            p = subprocess.run(["/usr/bin/python3", path], capture_output=True, text=True, timeout=60,
                               env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")})
            return json.dumps({"rc": p.returncode, "stdout": p.stdout[-12000:], "stderr": p.stderr[-3000:]},
                              ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": type(e).__name__ + ": " + str(e)}, ensure_ascii=False)
