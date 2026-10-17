from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class CurrentTime(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'current_time', 'description': '获取当前日期与时间（本地时间、UTC、时间戳、时区）。', 'parameters': {'type': 'object', 'properties': {'format': {'type': 'string', 'description': '可选，返回格式: iso / human / timestamp / all', 'enum': ['iso', 'human', 'timestamp', 'all']}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import json
        from datetime import datetime, timezone

        fmt = kwargs.get("format", "all")
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)

        data = {
            "local": now_local.strftime("%Y-%m-%d %H:%M:%S %z"),
            "utc": now_utc.strftime("%Y-%m-%d %H:%M:%S %z"),
            "timestamp": now_utc.timestamp(),
            "tzname": now_local.tzname(),
            "weekday": now_local.strftime("%A"),
        }

        if fmt == "iso":
            data = {"iso_local": now_local.isoformat(), "iso_utc": now_utc.isoformat()}
        elif fmt == "human":
            data = {"human": now_local.strftime("%Y-%m-%d %H:%M:%S"), "weekday": data["weekday"]}
        elif fmt == "timestamp":
            data = {"timestamp": data["timestamp"]}

        return json.dumps(data, ensure_ascii=False, indent=2)
