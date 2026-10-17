from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class GeoLocation(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'geo_location', 'description': '获取当前地理位置。默认使用本地离线方式（系统时区/配置推断所在地区）；可选 method=ip 通过外部 IP 定位服务获取经纬度与城市（会向第三方发送请求）。', 'parameters': {'type': 'object', 'properties': {'method': {'type': 'string', 'description': '定位方式: local（离线，默认，基于系统时区） / ip（联网，基于公网 IP，会请求第三方服务）', 'enum': ['local', 'ip']}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import json, os, subprocess

        method = kwargs.get("method", "local")

        if method == "local":
            result = {"method": "local", "source": "system timezone/config"}
            try:
                with open("/etc/timezone") as f:
                    result["timezone"] = f.read().strip()
            except Exception:
                try:
                    result["timezone"] = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
                except Exception:
                    pass
            result["locale"] = os.environ.get("LANG")
            try:
                out = subprocess.run(["timedatectl", "show", "--property=Timezone", "--property=LocalRTC"],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
                if out:
                    result["timedatectl"] = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
            except Exception:
                pass
            result["note"] = "离线方式仅能推断时区/地区，无法给出精确经纬度。如需精确坐标请使用 method=ip。"
            return json.dumps(result, ensure_ascii=False, indent=2)

        elif method == "ip":
            import urllib.request
            result = {"method": "ip", "source": "external IP geolocation service"}
            try:
                with urllib.request.urlopen("http://ip-api.com/json/?fields=status,message,country,regionName,city,zip,lat,lon,timezone,isp,query", timeout=8) as resp:
                    result["data"] = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
                result["hint"] = "可能无网络访问权限，或外部服务不可达。"
            return json.dumps(result, ensure_ascii=False, indent=2)

        return json.dumps({"error": f"unknown method: {method}"}, ensure_ascii=False)
