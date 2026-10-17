from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class SystemProbe(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'system_probe', 'description': '检测当前运行环境的系统信息（操作系统、Python 版本、主机名、CPU、内存、磁盘、关键环境变量等）。', 'parameters': {'type': 'object', 'properties': {'section': {'type': 'string', 'description': '可选，指定要返回的部分: os / python / hardware / env / all', 'enum': ['os', 'python', 'hardware', 'env', 'all']}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import sys, os, platform, socket, shutil, json, subprocess

        def _collect():
            info = {}

            # --- OS ---
            uname = platform.uname()
            info["os"] = {
                "system": uname.system,
                "node": uname.node,
                "release": uname.release,
                "version": uname.version,
                "machine": uname.machine,
                "processor": uname.processor,
                "platform": platform.platform(),
                "cwd": os.getcwd(),
                "pid": os.getpid(),
            }
            try:
                info["os"]["pretty"] = subprocess.run(
                    ["uname", "-a"], capture_output=True, text=True, timeout=5
                ).stdout.strip()
            except Exception:
                pass

            # --- Python ---
            info["python"] = {
                "version": sys.version,
                "executable": sys.executable,
                "implementation": platform.python_implementation(),
                "byteorder": sys.byteorder,
            }

            # --- Hardware ---
            hw = {}
            try:
                hw["cpu_count"] = os.cpu_count()
            except Exception:
                pass
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            hw["mem_total_kb"] = line.split()[1]
                            break
            except Exception:
                pass
            try:
                usage = shutil.disk_usage("/")
                hw["disk_root"] = {"total": usage.total, "used": usage.used, "free": usage.free}
            except Exception:
                pass
            try:
                hw["hostname"] = socket.gethostname()
            except Exception:
                pass
            info["hardware"] = hw

            # --- Env (筛选，避免泄露全部敏感值) ---
            keys = ["PATH", "HOME", "USER", "SHELL", "LANG", "PWD", "VIRTUAL_ENV", "PYTHONPATH"]
            info["env"] = {k: os.environ.get(k) for k in keys if k in os.environ}
            info["env_all_count"] = len(os.environ)

            return info

        section = kwargs.get("section", "all")
        data = _collect()
        if section and section != "all":
            data = {section: data.get(section, {})}
        return json.dumps(data, ensure_ascii=False, indent=2)
