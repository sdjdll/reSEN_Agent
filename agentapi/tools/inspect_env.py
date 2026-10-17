from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class InspectEnv(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'inspect_env', 'description': '只读排查工具：目录/文件检查、端口探测，以及对 HTTP 端点发 GET/POST 探测（看 Server 头与响应体）。用于定位协同设备/对端服务。', 'parameters': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'tree', 'read', 'probe', 'grep', 'http'], 'description': 'list/tree/read/grep/probe/http'}, 'path': {'type': 'string'}, 'pattern': {'type': 'string'}, 'host': {'type': 'string'}, 'ports': {'type': 'array', 'items': {'type': 'integer'}}, 'url': {'type': 'string', 'description': 'http 动作的目标 url'}, 'method': {'type': 'string', 'enum': ['GET', 'POST'], 'description': 'http 方法'}, 'body': {'type': 'object', 'description': 'POST 的 JSON 体'}}, 'required': ['action']}}}

    def run(self, *args, **kwargs):
        import os, json, socket, re, urllib.request, urllib.error

        root = os.getcwd()

        def safe(p):
            return os.path.abspath(p).startswith(os.path.abspath(root))

        action = kwargs.get("action", "list")

        if action == "list":
            d = kwargs.get("path") or root
            return json.dumps({"dir": d, "entries": [("dir:" if os.path.isdir(os.path.join(d,n)) else "file:")+n for n in sorted(os.listdir(d))]}, ensure_ascii=False, indent=2)

        if action == "tree":
            d = kwargs.get("path") or root
            lines = []
            for r, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x not in {".venv", ".git", "__pycache__", "node_modules"}]
                depth = r[len(d):].count(os.sep)
                lines.append("  "*depth + os.path.basename(r) + "/")
                for f in files:
                    lines.append("  "*(depth+1) + f)
            return json.dumps({"root": d, "tree": lines}, ensure_ascii=False, indent=2)

        if action == "read":
            p = kwargs.get("path", "")
            if not safe(p) or not os.path.isfile(p):
                return json.dumps({"error": "bad path"}, ensure_ascii=False)
            with open(p, errors="ignore") as f:
                txt = f.read()
            return json.dumps({"path": p, "content": txt[:9000]}, ensure_ascii=False, indent=2)

        if action == "grep":
            pat = kwargs.get("pattern", ""); d = kwargs.get("path") or root; hits = []
            for r, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x not in {".venv", ".git", "__pycache__"}]
                for f in files:
                    fp = os.path.join(r, f)
                    try:
                        for i, line in enumerate(open(fp, errors="ignore"), 1):
                            if re.search(pat, line):
                                hits.append(f"{fp}:{i}: {line.strip()[:200]}")
                    except Exception:
                        pass
            return json.dumps({"pattern": pat, "hits": hits[:120]}, ensure_ascii=False, indent=2)

        if action == "probe":
            host = kwargs.get("host", ""); ports = kwargs.get("ports", [22, 1124]); res = {}
            for port in ports:
                s = socket.socket(); s.settimeout(2.0)
                try:
                    s.connect((host, int(port))); res[int(port)] = "open"
                except Exception as e:
                    res[int(port)] = "closed/err: " + type(e).__name__
                finally:
                    s.close()
            return json.dumps({"host": host, "results": res}, ensure_ascii=False, indent=2)

        if action == "http":
            url = kwargs.get("url", ""); method = kwargs.get("method", "GET"); body = kwargs.get("body")
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            if data:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.dumps({"status": r.status, "headers": dict(r.headers), "body": r.read(3000).decode(errors="ignore")}, ensure_ascii=False, indent=2)
            except urllib.error.HTTPError as e:
                return json.dumps({"status": e.code, "headers": dict(e.headers), "body": e.read(3000).decode(errors="ignore")}, ensure_ascii=False, indent=2)
            except Exception as e:
                return json.dumps({"error": type(e).__name__ + ": " + str(e)}, ensure_ascii=False)

        return json.dumps({"error": "unknown action"}, ensure_ascii=False)
