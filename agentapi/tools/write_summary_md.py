from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class WriteSummaryMd(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'write_summary_md', 'description': '将分析总结写入 ~/temp/ 目录下的 markdown 文件（自动创建目录）。', 'parameters': {'type': 'object', 'properties': {'filename': {'type': 'string', 'description': '文件名（不含路径，须以 .md 结尾）'}, 'content': {'type': 'string', 'description': 'markdown 文本内容'}}, 'required': ['filename', 'content']}}}

    def run(self, *args, **kwargs):
        import os, json

        filename = kwargs["filename"]
        content = kwargs["content"]
        if not filename.endswith(".md"):
            filename += ".md"
        filename = os.path.basename(filename)

        target_dir = os.path.expanduser("~/temp")
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"written": path, "bytes": len(content.encode("utf-8"))}, ensure_ascii=False)
