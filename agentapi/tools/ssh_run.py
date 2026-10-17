from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool


class SshRun(hotplug_tool):
    def get_schema(self) -> dict:
        return {'type': 'function', 'function': {'name': 'ssh_run', 'description': 'SSH 协同工具：action=local 列出/读取本机 ~/.ssh（config、known_hosts、公钥）与 ssh-agent 状态；action=run 在目标主机上以 BatchMode 执行一条命令并返回输出。', 'parameters': {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['local', 'run'], 'description': 'local=看本机ssh；run=远程执行命令'}, 'host': {'type': 'string', 'description': 'run 的目标主机'}, 'user': {'type': 'string'}, 'command': {'type': 'string', 'description': 'run 的远程命令（单条字符串）'}, 'file': {'type': 'string', 'description': 'local 读取的具体文件路径'}}, 'required': ['action']}}}

    def run(self, *args, **kwargs):
        import os, json, subprocess

        def sh(cmd, timeout=15):
            try:
                p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                return p.returncode, p.stdout, p.stderr
            except Exception as e:
                return -1, "", type(e).__name__ + ": " + str(e)

        action = kwargs.get("action")

        if action == "local":
            out = {}
            sshdir = os.path.expanduser("~/.ssh")
            try:
                out["ls"] = sorted(os.listdir(sshdir))
            except Exception as e:
                out["ls"] = "err: " + str(e)
            for name in ("config", "known_hosts", "id_ed25519.pub", "id_ed25519"):
                p = os.path.join(sshdir, name)
                if os.path.isfile(p):
                    try:
                        txt = open(p, errors="ignore").read()
                        out[name] = txt[:4000] if "id_ed25519" not in name else "(private key, not shown; exists, mode=%o)" % (os.stat(p).st_mode & 0o777)
                    except Exception as e:
                        out[name] = "err: " + str(e)
            # ssh-agent keys
            rc, so, se = sh("ssh-add -l")
            out["ssh_add_l"] = {"rc": rc, "out": so.strip(), "err": se.strip()}
            # agent dir
            ad = os.path.join(sshdir, "agent")
            if os.path.isdir(ad):
                out["agent_dir"] = sorted(os.listdir(ad))
            return json.dumps(out, ensure_ascii=False, indent=2)

        if action == "run":
            host = kwargs.get("host"); user = kwargs.get("user", "sdjini"); command = kwargs.get("command", "echo ok")
            target = f"{user}@{host}"
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                   "-o", "StrictHostKeyChecking=accept-new", target, command]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                return json.dumps({"target": target, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}, ensure_ascii=False, indent=2)
            except Exception as e:
                return json.dumps({"target": target, "error": type(e).__name__ + ": " + str(e)}, ensure_ascii=False)

        return json.dumps({"error": "unknown action"}, ensure_ascii=False)
