from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Apibalance(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'apibalance', 'description': '读取 configure/config.json 里的 apikey/baseurl，调用 OpenAI 兼容的 /user/balance 接口查询账户余额（默认适配 DeepSeek），仅发起只读 GET 请求，不修改任何文件、不回显密钥。', 'parameters': {'properties': {'config': {'description': '配置文件相对路径，默认 configure/config.json', 'type': 'string'}}, 'required': [], 'type': 'object'}}}

    def run(self, *args, **kwargs):
        import json, os, urllib.request

        path = kwargs.get("config") or "configure/config.json"
        full = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)

        with open(full, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        key = cfg.get("apikey") or cfg.get("api_key")
        base = (cfg.get("baseurl") or "https://api.deepseek.com").rstrip("/")
        if not key:
            return "[错误] config.json 里没有找到 apikey"

        req = urllib.request.Request(
            base + "/user/balance",
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return f"[HTTP {e.code}] {e.read().decode('utf-8', 'ignore')[:500]}"
        except Exception as e:
            return f"[请求失败] {type(e).__name__}: {e}"

        lines = [f"baseurl: {base}"]
        lines.append(f"is_available: {data.get('is_available')}")
        for info in data.get("balance_infos", []) or []:
            lines.append(
                "币种 {currency}: 总余额 {total} / 赠金 {granted} / 充值余额 {topped}".format(
                    currency=info.get("currency"),
                    total=info.get("total_balance"),
                    granted=info.get("granted_balance"),
                    topped=info.get("topped_up_balance"),
                )
            )
        if len(lines) == 2:
            lines.append(json.dumps(data, ensure_ascii=False)[:800])
        return "\n".join(lines)
