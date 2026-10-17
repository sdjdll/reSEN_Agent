from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class RawHttp(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'raw_http', 'description': '对主机端口发原始 HTTP 请求并打印收到的原始字节（诊断对端服务行为：是否回响应、回什么）。', 'parameters': {'type': 'object', 'properties': {'host': {'type': 'string'}, 'port': {'type': 'integer'}, 'method': {'type': 'string'}, 'path': {'type': 'string'}, 'body': {'type': 'string'}, 'http_version': {'type': 'string'}}, 'required': ['host', 'port']}}}

    def run(self, *args, **kwargs):
        import socket, json

        host = kwargs.get("host"); port = int(kwargs.get("port"))
        method = kwargs.get("method", "POST")
        path = kwargs.get("path", "/chat")
        body = kwargs.get("body", "")
        ver = kwargs.get("http_version", "HTTP/1.1")

        head = f"{method} {path} {ver}\r\nHost: {host}\r\nConnection: close\r\n"
        if method == "POST":
            b = body.encode()
            head += "Content-Type: application/json\r\n" + f"Content-Length: {len(b)}\r\n"
        head += "\r\n"

        raw = b""
        status = "no-response"
        try:
            s = socket.socket(); s.settimeout(8)
            s.connect((host, port))
            s.sendall(head.encode() + body.encode())
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
            status = "closed-by-peer"
        except Exception as e:
            status = type(e).__name__ + ": " + str(e)
        finally:
            try: s.close()
            except Exception: pass

        return json.dumps({
            "sent": head + (body if method == "POST" else ""),
            "status": status,
            "bytes_received": len(raw),
            "raw_repr": raw[:2000].decode(errors="replace"),
        }, ensure_ascii=False, indent=2)
