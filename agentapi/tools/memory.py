from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Memory(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'memory', 'description': '双模长期记忆工具。scope=temp：对话暂存信息和产生的临时文件，随用随删，支持 set/get/list/update/delete/clear；scope=memory：需长期保留的关键信息，支持 set/get/list，但 update/delete 必须给出 reason（即信息不再成立）并写入审计日志。内部路径均相对 reSEN_Agent 根目录（history/temp、history/memory）。', 'parameters': {'properties': {'action': {'description': '操作: set 写入 / get 读取 / list 列出 / update 修改 / delete 删除 / clear 清空(仅 temp)', 'enum': ['set', 'get', 'list', 'update', 'delete', 'clear'], 'type': 'string'}, 'key': {'description': '记忆条目的键（唯一标识）', 'type': 'string'}, 'reason': {'description': '变更理由。scope=memory 时 update/delete 必填，须说明原信息为何不再成立', 'type': 'string'}, 'scope': {'description': '记忆区: temp 暂存 / memory 长期', 'enum': ['temp', 'memory'], 'type': 'string'}, 'source': {'description': '信息来源/上下文（set，可选）', 'type': 'string'}, 'tags': {'description': '标签列表', 'items': {'type': 'string'}, 'type': 'array'}, 'value': {'description': '记忆内容（set/update 必填）', 'type': 'string'}}, 'required': ['scope', 'action'], 'type': 'object'}}}

    def run(self, *args, **kwargs):
        import os, json
        from datetime import datetime

        # 相对根目录：reSEN_Agent（默认位于项目目录下；可用 RESEN_MEMORY_ROOT 覆盖根）
        ROOT = os.environ.get("RESEN_MEMORY_ROOT") or "/home/sdjini/Projects/reSEN-Agent"
        # 工具内部一律使用相对根目录的相对路径
        REL = {"temp": "history/temp", "memory": "history/memory"}
        DIRS = {k: os.path.join(ROOT, v) for k, v in REL.items()}
        STORE = {k: os.path.join(v, "store.json") for k, v in DIRS.items()}
        AUDIT = os.path.join(DIRS["memory"], "audit.log")


        def _now():
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


        def _load(scope):
            os.makedirs(DIRS[scope], exist_ok=True)
            p = STORE[scope]
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}


        def _save(scope, data):
            os.makedirs(DIRS[scope], exist_ok=True)
            with open(STORE[scope], "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)


        def _audit(action, key, old, new, reason):
            os.makedirs(DIRS["memory"], exist_ok=True)
            line = json.dumps({"time": _now(), "action": action, "key": key,
                               "old": old, "new": new, "reason": reason}, ensure_ascii=False)
            with open(AUDIT, "a", encoding="utf-8") as f:
                f.write(line + "\n")


        def _norm(scope):
            scope = (scope or "temp").lower()
            return scope if scope in DIRS else None


        scope = _norm(kwargs.get("scope"))
        action = (kwargs.get("action") or "list").lower()
        key = kwargs.get("key")
        value = kwargs.get("value")
        reason = (kwargs.get("reason") or "").strip()

        if not scope:
            return json.dumps({"error": "scope 必须是 temp 或 memory"}, ensure_ascii=False)

        # ---------- list ----------
        if action == "list":
            data = _load(scope)
            return json.dumps({"scope": scope, "rel_dir": REL[scope], "count": len(data),
                               "entries": [{"key": k, **v} for k, v in data.items()]},
                              ensure_ascii=False, indent=2)

        # ---------- get ----------
        if action == "get":
            if not key:
                return json.dumps({"error": "get 需要 key"}, ensure_ascii=False)
            data = _load(scope)
            if key not in data:
                return json.dumps({"error": f"未找到: {key}", "scope": scope}, ensure_ascii=False)
            return json.dumps({"scope": scope, "key": key, **data[key]}, ensure_ascii=False, indent=2)

        # ---------- set ----------
        if action == "set":
            if not key or value is None:
                return json.dumps({"error": "set 需要 key 与 value"}, ensure_ascii=False)
            data = _load(scope)
            if scope == "memory" and key in data:
                return json.dumps({
                    "error": f"长期记忆 '{key}' 已存在。仅当该信息不再成立时，使用 action=update 并附 reason 修改。",
                    "current": data[key]}, ensure_ascii=False)
            entry = data.get(key, {})
            entry.update({
                "value": value,
                "tags": kwargs.get("tags", entry.get("tags", [])),
                "source": kwargs.get("source", entry.get("source", "")),
                "created": entry.get("created", _now()),
                "updated": _now(),
            })
            data[key] = entry
            _save(scope, data)
            if scope == "memory":
                _audit("set", key, None, value, kwargs.get("source", "") and "来源:" + str(kwargs.get("source", "")))
            return json.dumps({"scope": scope, "saved": key, "rel_path": os.path.join(REL[scope], "store.json")},
                              ensure_ascii=False)

        # ---------- update ----------
        if action == "update":
            if not key or value is None:
                return json.dumps({"error": "update 需要 key 与 value"}, ensure_ascii=False)
            data = _load(scope)
            if key not in data:
                return json.dumps({"error": f"未找到: {key}，请先用 set 建立"}, ensure_ascii=False)
            if scope == "memory" and not reason:
                return json.dumps({
                    "error": "修改长期记忆必须提供 reason（说明为何原信息不再成立），并会记入审计日志。"},
                    ensure_ascii=False)
            old = data[key].get("value")
            data[key].update({"value": value,
                              "tags": kwargs.get("tags", data[key].get("tags", [])),
                              "updated": _now()})
            _save(scope, data)
            if scope == "memory":
                _audit("update", key, old, value, reason)
            return json.dumps({"scope": scope, "updated": key, "old": old, "new": value},
                              ensure_ascii=False)

        # ---------- delete ----------
        if action == "delete":
            if not key:
                return json.dumps({"error": "delete 需要 key"}, ensure_ascii=False)
            data = _load(scope)
            if key not in data:
                return json.dumps({"error": f"未找到: {key}"}, ensure_ascii=False)
            if scope == "memory" and not reason:
                return json.dumps({
                    "error": "删除长期记忆必须提供 reason（说明该信息为何不再成立），并会记入审计日志。"},
                    ensure_ascii=False)
            old = data.pop(key)
            _save(scope, data)
            if scope == "memory":
                _audit("delete", key, old.get("value"), None, reason)
            return json.dumps({"scope": scope, "deleted": key}, ensure_ascii=False)

        # ---------- clear（仅暂存可用）----------
        if action == "clear":
            if scope == "memory":
                return json.dumps({"error": "长期记忆不支持批量清空，请逐条 delete 并说明理由。"},
                                  ensure_ascii=False)
            n = len(_load(scope))
            _save(scope, {})
            return json.dumps({"scope": scope, "cleared": n}, ensure_ascii=False)

        return json.dumps({"error": f"未知 action: {action}"}, ensure_ascii=False)
