from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Sysenv(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'sysenv', 'description': '只读环境探测：返回操作系统、Python 版本、工作目录、CPU/内存/磁盘、以及关键环境变量等信息，不修改任何文件。', 'parameters': {'type': 'object', 'properties': {'section': {'type': 'string', 'enum': ['all', 'os', 'python', 'disk', 'mem', 'env'], 'description': '要查看的片段，默认 all'}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import os, sys, platform, shutil, textwrap
        section = (kwargs.get("section") or (args[0] if args else "all") or "all").lower()
        out = {}

        def os_info():
            out["system"] = platform.system()
            out["release"] = platform.release()
            out["version"] = platform.version()
            out["machine"] = platform.machine()
            out["node"] = platform.node()
            try:
                out["distro"] = platform.freedesktop_os_release().get("PRETTY_NAME")
            except Exception:
                pass
            out["cpu_count"] = os.cpu_count()

        def py_info():
            out["python"] = sys.version.replace("\n", " ")
            out["executable"] = sys.executable
            out["cwd"] = os.getcwd()
            out["platform_module"] = getattr(sys, "platform", None)
            out["in_venv"] = sys.prefix != getattr(sys, "base_prefix", sys.prefix)

        def disk_info():
            try:
                u = shutil.disk_usage(os.getcwd())
                out["disk_total_gb"] = round(u.total / 2**30, 1)
                out["disk_used_gb"] = round(u.used / 2**30, 1)
                out["disk_free_gb"] = round(u.free / 2**30, 1)
                out["disk_percent"] = round(u.used / u.total * 100, 1)
            except Exception as e:
                out["disk_error"] = str(e)

        def mem_info():
            try:
                with open("/proc/meminfo") as f:
                    d = {k.strip(): v for k, v in (l.split(":", 1) for l in f if ":" in l)}
                tot = int(d["MemTotal"].split()[0]) / 2**20
                avail = int(d["MemAvailable"].split()[0]) / 2**20
                out["mem_total_gb"] = round(tot, 1)
                out["mem_available_gb"] = round(avail, 1)
                out["mem_used_percent"] = round((tot - avail) / tot * 100, 1)
                out["loadavg"] = [round(x, 2) for x in os.getloadavg()]
            except Exception as e:
                out["mem_error"] = str(e)

        def env_info():
            keep = ("PATH", "HOME", "USER", "SHELL", "LANG", "TZ", "VIRTUAL_ENV", "CONDA_PREFIX", "HTTP_PROXY", "HTTPS_PROXY")
            for k in keep:
                if k in os.environ:
                    v = os.environ[k]
                    out[k] = v if len(v) < 160 else v[:157] + "..."
            # 用相对本文件的路径解析，不依赖进程 cwd（否则从仓库根启动时永远找不到）
            _here = os.path.dirname(os.path.abspath(__file__))       # .../agentapi/tools
            _root = os.path.dirname(os.path.dirname(_here))          # 仓库根
            try:
                out["tools_dir"] = sorted(
                    f[:-3] for f in os.listdir(_here) if f.endswith(".py") and f != "__init__.py"
                )
            except Exception:
                out["tools_dir"] = None
            try:
                out["skills_dir"] = sorted(os.listdir(os.path.join(_root, "skills")))
            except Exception:
                out["skills_dir"] = None

        if section in ("all", "os"):
            os_info()
        if section in ("all", "python"):
            py_info()
        if section in ("all", "disk"):
            disk_info()
        if section in ("all", "mem"):
            mem_info()
        if section in ("all", "env"):
            env_info()

        lines = ["【环境探测结果】"]
        for k, v in out.items():
            lines.append("- %s: %s" % (k, v))
        return "\n".join(lines)
