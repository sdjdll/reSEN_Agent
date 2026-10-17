from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class NetInfo(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'net_info', 'description': '获取本机网络信息：主机名、各网卡 IP、默认路由到外网时的本机出口 IP（用于判断 10.10.10.100 是否为本人机器）。', 'parameters': {'type': 'object', 'properties': {}}}}

    def run(self, *args, **kwargs):
        import socket, json

        info = {"hostname": socket.gethostname()}
        try:
            info["fqdn"] = socket.getfqdn()
        except Exception:
            pass

        ips = set()
        try:
            for res in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(res[4][0])
        except Exception as e:
            info["addr_err"] = str(e)

        for probe in ("10.10.10.100", "8.8.8.8", "1.1.1.1"):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((probe, 80))
                ips.add(s.getsockname()[0])
            except Exception:
                pass
            finally:
                s.close()

        info["ips"] = sorted(ips)

        try:
            info["if_inet"] = open("/proc/net/fib_trie").read().count("10.10.10.100")
        except Exception:
            pass

        return json.dumps(info, ensure_ascii=False, indent=2)
