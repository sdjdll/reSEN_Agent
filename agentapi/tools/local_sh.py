from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class LocalSh(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'local_sh', 'description': '在本机执行一条 shell 命令并返回退出码、stdout、stderr（用于排查本机与已连接设备，如 adb）。', 'parameters': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': '要执行的 shell 命令'}, 'timeout': {'type': 'integer', 'description': '超时秒数，默认 30'}}, 'required': ['command']}}}

    def run(self, *args, **kwargs):
        import json, subprocess

        cmd = kwargs.get("command") or (args[0] if args else "")
        timeout = int(kwargs.get("timeout") or 30)
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return json.dumps({
                "command": cmd,
                "rc": p.returncode,
                "stdout": p.stdout[-6000:],
                "stderr": p.stderr[-2000:],
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"command": cmd, "error": type(e).__name__ + ": " + str(e)}, ensure_ascii=False)
