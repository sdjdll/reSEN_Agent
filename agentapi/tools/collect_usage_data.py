from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class CollectUsageData(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'collect_usage_data', 'description': '采集本机设备使用习惯相关的聚合统计数据（只读）：shell 命令历史频率与活跃时段、登录会话记录、系统启动记录、家目录文件按小时活跃分布、已安装/常用应用线索。返回 JSON。', 'parameters': {'type': 'object', 'properties': {'include': {'type': 'string', 'description': '要采集的部分: shell / sessions / files / apps / all', 'enum': ['shell', 'sessions', 'files', 'apps', 'all']}}, 'required': []}}}

    def run(self, *args, **kwargs):
        import json, os, re, subprocess
        from collections import Counter
        from datetime import datetime

        home = os.path.expanduser("~")
        inc = kwargs.get("include", "all")

        def run(cmd, timeout=10):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
            except Exception:
                return ""

        out = {}

        # ---------- Shell history ----------
        if inc in ("shell", "all"):
            hist = {}
            # bash
            try:
                with open(os.path.join(home, ".bash_history"), errors="ignore") as f:
                    lines = [l.strip() for l in f if l.strip()]
                cmds = Counter()
                for l in lines:
                    # skip timestamps like "#1697000000"
                    if l.startswith("#"):
                        continue
                    base = l.split()[0] if l.split() else l
                    cmds[base] += 1
                hist["bash"] = {
                    "total_entries": len(lines),
                    "top_commands": cmds.most_common(25),
                }
            except FileNotFoundError:
                hist["bash"] = "not found"
            # zsh
            zh = os.path.join(home, ".zsh_history")
            if os.path.exists(zh):
                try:
                    with open(zh, errors="ignore") as f:
                        zlines = [l.strip() for l in f if l.strip()]
                    zcmds = Counter()
                    for l in zlines:
                        # zsh extended format: ": 1697000000:0;cmd"
                        m = re.match(r"^:\s*(\d+):\d+;(.*)$", l)
                        body = m.group(2) if m else l
                        if body.split():
                            zcmds[body.split()[0]] += 1
                    hist["zsh"] = {"total_entries": len(zlines), "top_commands": zcmds.most_common(25)}
                except Exception as e:
                    hist["zsh"] = f"error: {e}"
            out["shell_history"] = hist

        # ---------- Sessions (last/wtmp) ----------
        if inc in ("sessions", "all"):
            last = run(["last", "-n", "50", "-F"])
            logins = []
            for line in last.splitlines():
                if line.startswith("wtmp") or not line.strip():
                    continue
                logins.append(line)
            boots = run(["journalctl", "--list-boots", "--no-pager"])
            out["sessions"] = {
                "recent_logins": logins[:30],
                "recent_login_count": len(logins),
                "boots_tail": boots.strip().splitlines()[-15:],
            }

        # ---------- File activity by hour ----------
        if inc in ("files", "all"):
            hour_counter = Counter()
            dow_counter = Counter()
            ext_counter = Counter()
            scanned = 0
            cutoff = datetime.now().timestamp() - 60 * 60 * 24 * 30  # last 30 days
            skip_dirs = {".cache", ".git", "node_modules", ".venv", "venv", "site-packages", ".mozilla", ".config"}
            for root, dirs, files in os.walk(home):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".cache")]
                for fn in files:
                    p = os.path.join(root, fn)
                    try:
                        st = os.stat(p)
                    except Exception:
                        continue
                    if st.st_mtime < cutoff:
                        continue
                    scanned += 1
                    dt = datetime.fromtimestamp(st.st_mtime)
                    hour_counter[dt.hour] += 1
                    dow_counter[dt.strftime("%A")] += 1
                    if "." in fn:
                        ext_counter[fn.rsplit(".", 1)[-1].lower()] += 1
                    if scanned > 50000:
                        break
                if scanned > 50000:
                    break
            out["file_activity"] = {
                "files_modified_last_30d_sampled": scanned,
                "by_hour": sorted(hour_counter.items()),
                "by_weekday": dow_counter.most_common(),
                "top_extensions": ext_counter.most_common(15),
            }

        # ---------- Apps ----------
        if inc in ("apps", "all"):
            apps = {}
            # running processes top by name
            ps = run(["ps", "-eo", "comm", "--no-headers"])
            pc = Counter(l.strip() for l in ps.splitlines() if l.strip())
            apps["top_process_comm"] = pc.most_common(25)
            # installed pacman packages count + recently installed
            apps["pacman_explicit_count"] = run(["bash", "-lc", "pacman -Qe 2>/dev/null | wc -l"]).strip()
            apps["pacman_recent"] = run(["bash", "-lc", "grep -E 'installed|upgraded' /var/log/pacman.log 2>/dev/null | tail -15"]).strip().splitlines()
            # desktop entries (GUI apps available)
            de = run(["bash", "-lc", "ls /usr/share/applications/*.desktop 2>/dev/null | wc -l"]).strip()
            apps["desktop_entries_count"] = de
            # systemd user units enabled (autostart-ish)
            apps["user_units_enabled"] = run(["bash", "-lc", "systemctl --user list-unit-files --state=enabled --no-pager 2>/dev/null | head -20"]).strip().splitlines()
            out["apps"] = apps

        return json.dumps(out, ensure_ascii=False, indent=2)
