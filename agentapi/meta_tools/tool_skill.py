from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class Skill(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'skill', 'description': '技能库管理工具：把可复用的技能/流程以 Markdown（YAML frontmatter: name/description/tags/created/updated）形式存于 reSEN_Agent 根目录下的相对路径 skills/ 中，支持 list/create/read/update/delete/search 六种操作。', 'parameters': {'properties': {'action': {'description': '操作类型: list 列出全部技能 / create 新建技能 / read 读取技能全文 / update 更新技能 / delete 删除技能 / search 按关键词检索', 'enum': ['list', 'create', 'read', 'update', 'delete', 'search'], 'type': 'string'}, 'content': {'description': '技能正文，Markdown（create/update）', 'type': 'string'}, 'description': {'description': '技能一句话描述（create/update）', 'type': 'string'}, 'keyword': {'description': 'search 的检索关键词,匹配 name/description/tags/content', 'type': 'string'}, 'name': {'description': '技能名（create 必填；read/update/delete 用 name 或 slug 定位）', 'type': 'string'}, 'overwrite': {'description': 'create 时若同名技能已存在是否覆盖，默认 false', 'type': 'boolean'}, 'slug': {'description': '技能文件名（不含 .md），用于定位已有技能；缺省时由 name 推导', 'type': 'string'}, 'tags': {'description': '标签列表（create/update）', 'items': {'type': 'string'}, 'type': 'array'}}, 'required': ['action'], 'type': 'object'}}}

    def run(self, *args, **kwargs):
        import os, re, json
        from datetime import datetime

        ROOT = os.environ.get("RESEN_ROOT") or "/home/sdjini/Projects/reSEN-Agent"
        REL_DIR = "skills"
        SKILLS_DIR = os.path.join(ROOT, REL_DIR)


        def _slugify(text):
            text = (text or "").strip().lower()
            text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text)
            text = re.sub(r"-{2,}", "-", text).strip("-")
            return text or "skill"


        def _path(slug):
            return os.path.join(SKILLS_DIR, slug + ".md")


        def _parse(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            meta, body = {}, raw
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                if len(parts) >= 3:
                    fm, body = parts[1], parts[2].lstrip("\n")
                    for line in fm.splitlines():
                        if ":" not in line:
                            continue
                        k, v = line.split(":", 1)
                        k, v = k.strip(), v.strip()
                        if v.startswith("[") and v.endswith("]"):
                            v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                        meta[k] = v
            meta["slug"] = os.path.splitext(os.path.basename(path))[0]
            meta["body"] = body
            return meta


        def _render(name, description, tags, body, created):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tags_s = ", ".join(tags or [])
            fm = (
                "---\n"
                f"name: {name}\n"
                f"description: {description or ''}\n"
                f"tags: [{tags_s}]\n"
                f"created: {created or now}\n"
                f"updated: {now}\n"
                "---\n\n"
            )
            return fm + (body or "").rstrip() + "\n"


        def _list_skills():
            if not os.path.isdir(SKILLS_DIR):
                return []
            out = []
            for fn in sorted(os.listdir(SKILLS_DIR)):
                if fn.endswith(".md"):
                    out.append(_parse(os.path.join(SKILLS_DIR, fn)))
            return out


        action = kwargs.get("action", "list")
        name = kwargs.get("name")
        slug = kwargs.get("slug") or (_slugify(name) if name else None)

        if action == "list":
            skills = _list_skills()
            return json.dumps({
                "rel_dir": REL_DIR,
                "count": len(skills),
                "skills": [{"slug": s["slug"], "name": s.get("name", s["slug"]),
                            "description": s.get("description", ""),
                            "tags": s.get("tags", []), "updated": s.get("updated", "")}
                           for s in skills],
            }, ensure_ascii=False, indent=2)

        if action == "search":
            kw = (kwargs.get("keyword") or "").lower()
            hits = []
            for s in _list_skills():
                hay = " ".join([str(s.get("name", "")), str(s.get("description", "")),
                                " ".join(s.get("tags", []) or []), s.get("body", "")]).lower()
                if kw in hay:
                    hits.append({"slug": s["slug"], "name": s.get("name", s["slug"]),
                                 "description": s.get("description", ""), "tags": s.get("tags", [])})
            return json.dumps({"keyword": kw, "count": len(hits), "results": hits},
                              ensure_ascii=False, indent=2)

        if action == "create":
            if not name:
                return json.dumps({"error": "create 需要 name"}, ensure_ascii=False)
            os.makedirs(SKILLS_DIR, exist_ok=True)
            path = _path(slug)
            if os.path.exists(path) and not kwargs.get("overwrite"):
                return json.dumps({"error": f"技能已存在: {slug}（如需覆盖请传 overwrite=true）"},
                                  ensure_ascii=False)
            created = None
            if os.path.exists(path):
                created = _parse(path).get("created")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_render(name, kwargs.get("description", ""),
                                kwargs.get("tags", []), kwargs.get("content", ""), created))
            return json.dumps({"created": os.path.join(REL_DIR, slug + ".md"), "slug": slug},
                              ensure_ascii=False)

        if action in ("read", "update", "delete"):
            if not slug:
                return json.dumps({"error": f"{action} 需要 name 或 slug"}, ensure_ascii=False)
            path = _path(slug)
            if not os.path.exists(path):
                return json.dumps({"error": f"技能不存在: {slug}"}, ensure_ascii=False)

            if action == "read":
                s = _parse(path)
                return json.dumps(s, ensure_ascii=False, indent=2)

            if action == "delete":
                os.remove(path)
                return json.dumps({"deleted": os.path.join(REL_DIR, slug + ".md")}, ensure_ascii=False)

            s = _parse(path)
            new_name = name or s.get("name", slug)
            new_desc = kwargs.get("description", s.get("description", ""))
            new_tags = kwargs.get("tags", s.get("tags", []))
            new_body = kwargs.get("content", s.get("body", ""))
            with open(path, "w", encoding="utf-8") as f:
                f.write(_render(new_name, new_desc, new_tags, new_body, s.get("created")))
            return json.dumps({"updated": os.path.join(REL_DIR, slug + ".md"), "slug": slug},
                              ensure_ascii=False)

        return json.dumps({"error": f"未知 action: {action}"}, ensure_ascii=False)
