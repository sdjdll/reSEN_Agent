from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Cfgpeek(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'cfgpeek', 'description': '只读查看 reSEN_Agent 根目录下的配置文件（默认 configure/config.json），自动对疑似密钥字段打码，不修改任何文件。', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': '相对根目录的路径，默认 configure/config.json'}, 'show_secrets': {'type': 'boolean', 'description': '是否显示明文密钥，默认 false（打码）'}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import json, os, re

        path = kwargs.get("path") or "configure/config.json"
        show = bool(kwargs.get("show_secrets", False))

        root = os.getcwd()
        full = path if os.path.isabs(path) else os.path.join(root, path)
        if not os.path.exists(full):
            return f"[未找到] {full}\n当前目录: {root}"

        with open(full, "r", encoding="utf-8") as f:
            raw = f.read()

        def mask(s):
            s = str(s)
            if len(s) <= 10:
                return s[:2] + "***"
            return s[:6] + "..." + s[-4:] + f" (len={len(s)})"

        SECRET_KEYS = re.compile(r"(key|token|secret|password|passwd|auth)", re.I)

        def walk(o):
            if isinstance(o, dict):
                out = {}
                for k, v in o.items():
                    if isinstance(v, (dict, list)):
                        out[k] = walk(v)
                    elif not show and isinstance(v, str) and SECRET_KEYS.search(k) and v:
                        out[k] = mask(v)
                    else:
                        out[k] = v
                return out
            if isinstance(o, list):
                return [walk(i) for i in o]
            return o

        try:
            data = json.loads(raw)
            body = json.dumps(walk(data), ensure_ascii=False, indent=2)
        except Exception:
            body = "\n".join(
                (re.sub(r"(:\s*)(\"[^\"]{8,}\")", lambda m: m.group(1) + mask(m.group(2).strip('"')), ln)
                 if not show else ln)
                for ln in raw.splitlines()
            )

        return f"[{full}] ({len(raw)} bytes)\n{body}"
