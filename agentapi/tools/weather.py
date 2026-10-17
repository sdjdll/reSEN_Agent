from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Weather(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'weather', 'description': '查询指定经纬度的当前天气（基于 Open-Meteo 免费接口，无需 API Key）。返回温度、体感温度、湿度、风速、天气状况等。', 'parameters': {'type': 'object', 'properties': {'latitude': {'type': 'number', 'description': '纬度'}, 'longitude': {'type': 'number', 'description': '经度'}}, 'required': ['latitude', 'longitude']}}}

    def run(self, *args, **kwargs):
        import json, urllib.request, urllib.parse

        lat = kwargs.get("latitude")
        lon = kwargs.get("longitude")
        if lat is None and len(args) >= 1:
            lat = args[0]
        if lon is None and len(args) >= 2:
            lon = args[1]

        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
        })

        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        codes = {
            0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴",
            45: "雾", 48: "雾凇",
            51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨",
            71: "小雪", 73: "中雪", 75: "大雪",
            80: "小阵雨", 81: "阵雨", 82: "强阵雨",
            95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
        }
        cur = data.get("current", {})
        code = cur.get("weather_code")
        return {
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "timezone": data.get("timezone"),
            "time": cur.get("time"),
            "temperature_C": cur.get("temperature_2m"),
            "apparent_temperature_C": cur.get("apparent_temperature"),
            "humidity_percent": cur.get("relative_humidity_2m"),
            "wind_speed_kmh": cur.get("wind_speed_10m"),
            "weather_code": code,
            "weather": codes.get(code, "未知"),
        }
