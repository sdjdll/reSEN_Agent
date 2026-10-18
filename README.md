# reSEN-Agent

基于 OpenAI 兼容接口的终端 AI Agent：Textual TUI + 可热插拔工具 + 多会话长期记忆 + 局域网 peer 协同。

> 模型只需支持 OpenAI Chat Completions 协议即可（`baseurl` 可指向任意兼容服务）。

## 功能特性

- **终端 TUI**：流式输出、思维链折叠展示、Markdown 渲染、token 用量显示
- **工具审批**：每次工具调用可 `Allow` / `Allow All` / `Deny`，运行中可随时停止
- **元工具 + 热插拔工具**：AI 可在运行期创建、搜索、查看、删除、执行工具，无需重启
- **多会话**：新建 / 切换 / 删除会话，切换模型、推理强度、额外请求参数
- **上下文压缩**：一键把历史对话压缩成摘要，释放上下文窗口
- **双模记忆**：`temp` 暂存随用随删；`memory` 长期记忆的修改/删除必须给出理由并写入审计日志
- **技能库**：以 Markdown（YAML frontmatter）保存可复用流程，支持增删改查与检索
- **peer 协同**：内置 HTTP 服务（默认 `0.0.0.0:1124`），多个实例可互相调用；连接过的设备记入 `configure/connect.json`
- **跨平台启动**：`start.sh` / `start.ps1` / `start.bat`

## 快速开始

### 1. 环境要求

- Python **3.10+**
- 可访问的 OpenAI 兼容 API

### 2. 配置

编辑 `configure/config.json`：

```json
{
  "apikey": "sk-你的密钥",
  "baseurl": "https://api.openai.com/v1",
  "modelname": "gpt-4o-mini",
  "access_key": "peer-共享密钥",
  "config": {
    "reasoning_effort": "medium",
    "context_limit": 128000,
    "peer_serve": true,
    "peer_tools": true,
    "extra": [
      { "thinking": { "type": "enabled" } },
      { "thinking": { "type": "disabled" } }
    ]
  }
}
```

| 键 | 说明 |
| --- | --- |
| `apikey` | OpenAI 兼容服务的 API Key |
| `baseurl` | 服务地址，需以 `/v1` 结尾（视服务而定） |
| `modelname` | 默认模型名，TUI 中可点击切换 |
| `access_key` | peer 服务鉴权密钥；设为空字符串表示不鉴权，保持 `NO-ACCESS_KEY` 则拒绝启动 peer |
| `config.reasoning_effort` | 推理强度：`none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max` |
| `config.context_limit` | 上下文上限（token），用于底部用量显示 |
| `config.peer_serve` | 启动 TUI 时是否自动拉起 peer 服务（默认 `true`） |
| `config.peer_tools` | 是否允许远端 peer 驱动本地工具（默认 `true`） |
| `config.extra` | 额外请求体列表，TUI 中可切换；如 `thinking` 开关 |

### 3. 安装与启动

**Linux / macOS**

```bash
./start.sh          # 自动创建 .venv、安装依赖并启动
./start.sh --check  # 只校验环境、配置与依赖
```

可用环境变量 `PYTHON` 指定解释器，例如 `PYTHON=python3.12 ./start.sh`。

**Windows**

```bat
start.bat
start.ps1
start.bat --check
```

**手动启动**

```bash
python -m venv .venv
.venv/bin/pip install -r requirement
.venv/bin/python tui/main.py
```

## 使用

### 快捷键

| 按键 | 作用 |
| --- | --- |
| `Enter` | 发送消息 / 确认 |
| `↑` `↓` | 历史消息滚动 |
| `PgUp` `PgDn` | 历史分页 |
| `Ctrl+P` | 命令面板（设置、会话、peer 服务） |
| `F2` | 打开设置表格 |
| `Ctrl+Z` | 挂起进程 |
| `Ctrl+Q` | 退出（退出前自动落盘） |
| `Esc` | 关闭弹窗 |

对话进行中，`Send` 按钮会变成 `Cancel`，点击即可中止本轮并丢弃未执行的工具调用。

### 底部状态栏

依次显示：模型名、推理强度、`extra` 参数、上下文用量。点击模型名可循环切换可用模型，点击推理强度可循环切换，点击 `extra` 可切换预设，点击 `ctx` 可压缩当前会话历史。

### 命令面板（`Ctrl+P`）

- **设置**：列出 `config.json` 的所有配置项，回车修改
- **会话**：新建、切换、删除会话
- **peer**：启动/停止 peer 服务、切换开机自启

## 工具系统

### 元工具

元工具写死在 `agentapi/meta_tools/`，启动时注册 schema：

| 工具 | 作用 |
| --- | --- |
| `tool_create` | 根据名称、描述、参数 JSON Schema 和实现代码生成新的热插拔工具 |
| `tool_search` | 列出或按关键字搜索 `tools/` 下的工具 |
| `tool_view` | 查看某个工具的 schema 与源码 |
| `tool_delete` | 删除某个工具模块 |
| `tool_run` | 统一执行器：按名运行任意元工具或热插拔工具 |
| `tool_skill` | 技能库管理：列出 / 新建 / 读取 / 更新 / 删除 / 检索 |
| `peer_agent` | 调用远端 reSEN-Agent 实例并取回回复 |

### 热插拔工具

位于 `agentapi/tools/`，由 AI 在运行期按需生成，也可以手动添加。必须继承 `hotplug_tool` 并实现 `get_schema()` 与 `run()`：

```python
from agentapi.meta_tools.interface.hotplug_tool import hotplug_tool

class CurrentTime(hotplug_tool):
    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "current_time",
                "description": "获取当前时间",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def run(self, *args, **kwargs):
        ...
```

工具目录是动态的：

- `agentapi/tools/` 由 AI 通过 `tool_create` / `tool_delete` 在运行期增删，**目录为空是正常状态**，不代表功能缺失
- 工具无需预先注册；`tool_run` 按名称即时加载并执行
- 调用前可用 `tool_search` 查看当前已有工具，用 `tool_view` 查看其 schema 与源码
- `tool_create` 会生成继承 `hotplug_tool` 的模块，并在落盘前做语法校验

## 记忆与技能

| 路径 | 用途 |
| --- | --- |
| `configure/config.json` | 主配置（API Key、模型、peer 设置） |
| `configure/memory.json` | 多会话消息链与当前会话 |
| `configure/connect.json` | 已连接的 peer 设备 |
| `history/temp/store.json` | 临时记忆，可 `clear` |
| `history/memory/store.json` | 长期记忆 |
| `history/memory/audit.log` | 长期记忆变更审计日志 |
| `skills/*.md` | 技能库文件 |

长期记忆的 `update` / `delete` 必须提供 `reason`，用于说明原信息为何不再成立。

## peer 协同

每个实例都可以作为 peer 服务端，让其他实例通过 `peer_agent` 工具调用：

```bash
python api/peer_api.py                 # 监听 0.0.0.0:1124
python api/peer_api.py --port 1124 --key mykey
python api/peer_api.py --no-tools      # 禁止远端驱动本地工具
python api/peer_api.py --probe http://192.168.1.7:1124
```

请求格式：

```json
{
  "messages": [{ "role": "user", "content": "你好" }],
  "access_key": "peer-共享密钥"
}
```

`peer_agent` 工具只需要提供 `url`（省略端口时默认 `1124`），可选 `content` 或完整 `messages`、`access_key`、`name`、`desc`、`tag`。连接成功的设备会写入 `configure/connect.json`。

## 目录结构

```
agentapi/
  agentapi.py          OpenAI 接口封装（chat / chat_stream / tool / deny / models）
  core.py              会话、配置、记忆、上下文压缩、流式渲染
  meta_tools/          元工具与工具接口
  tools/               热插拔工具（运行期动态生成，可为空）
api/
  peer_api.py          peer HTTP 服务端与客户端
tui/
  main.py              Textual 主界面
  setpanel.py          设置、会话、peer 命令面板
file/
  dick.py              点路径配置读写
  jsonfile.py          JSON 文件读写
configure/             配置与会话数据
history/               记忆存储
skills/                技能库
start.sh / start.ps1 / start.bat
```

## 安全提示

- 动态生成的工具可能具备执行 shell、SSH、桌面注入、网络请求等高危能力；执行前请用 `tool_view` 检查其 schema 与源码，并只在可信环境中使用
- `peer_agent` 会把内容发送到指定主机，只连接你信任的 peer；务必设置非空 `access_key`
- 仓库已忽略 `__pycache__/` 与 `*.pyc`

## 许可证

[MIT](LICENSE)
