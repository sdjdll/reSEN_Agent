# agentapi `原chatapi.py`
> **agentapi是一个agent核心，目的是上层能够简单快速地实现agent自行迭代升级**

### 1. 如何实现？
- 该包引入了`meta_tool(元工具)`和`hotplug_tool(热插拔工具)`的概念 ，以便实现不用重启也能直接加载新工具的能力

### 2. 元工具？
- 元工具`meta_tool(metatool)`可以看作是原本agent的工具，直接写死在代码中，必须重载才能加载进agent
- 元工具需要实现对热插拔工具的操作，并且保护meta_tools不被修改，以维护ai幻觉时agentapi的运行稳定
- 如果有办法注入schema或者需要灵活性，元工具并不强制要求继承[meta_tool](./meta_tools/interface/meta_tool.py)，不过为了统一调用，建议继承并实现相关方法
### 3. 热插拔工具？
- 热插拔工具`hotplug_tool`是`agentapi`的核心，由ai在运行过程中按需编写
- 不能直接调用，而是通过[元工具run](./meta_tools/tool_run.py)进行调用
- 所有热插拔工具**必须继承**[hotplug_tool](./meta_tools/interface/hotplug_tool.py)，实现所有abstract方法，否则可能出现无法调用等问题
### 4. 流程？
1. 大模型返回ccmp
    - 带tool_call
    2. 提升为tool
    3. 调用对应工具
    4. 返回工具结果给上层
    - 不带tool_call
    2. 返回ccmp

## [agentapi.py](./agentapi.py)
主文件，将openai接口抽象为chat和tool两个主要方法
1. .chat(usrmsg: list[ccmp]) async
    - 传入一个list[ccmp]作为上下文申请回复
    - 不管理上下文
    - 返回完整ccmp，由上层自行管理
    - 抛弃旧的 ~~`function_call`~~ 字段
    - 使用了AsyncOpenAI，推荐异步
    ``` python
    # init history:list[ccmp] = [init msg]
    client:chat_api = chat_api(
            openai=openai(
                base_url="BaseURL",
                api_key="APIKey"
            ),
            model_name="ModelName"
        )
    reply:ccmp = client.chat(history)
    ```
2. .chat_stream(usrmsg: list[ccmp]) async
    - 传入一个list[ccmp]作为上下文申请回复
    - 不管理上下文
    - 返回`AsyncChatCompletionStreamManager`，由上层解析
    - 抛弃旧的 ~~`function_call`~~ 字段
    - 使用了AsyncOpenAI，推荐异步
3. .tool(message: ccmp)
    - 传入带有一个或多个tool_call的ccmp，用于提升为工具调用
        ``` python
        from openai.types.chat import ChatCompletionMessageParam as ccmp 
        # agentapi = agentapi()
        reply:ccmp = await agentapi.chat(history)
        # 当reply中有tool_call时，直接将chat的输出通过tool回传给agentapi即可
        if reply.get("tool_calls"):
            toolrst:ccmp = agentapi.tool(reply)
        # 此时tool会根据工具调用自行运行工具
        ```
    - 返回带有所有tool_call的结果(或报错)的list[ccmp]
    - tool报错在ccmp中，使用try-catch无法捕捉异常，如需捕捉异常需要解析ccmp

3. .get_schemas()
    - 获取所有元工具的schema
    - 内部直接传入了模型中，无需再传

## [tools](./tools/__init__.py)
热插拔工具的存放目录

## [meta_tools](./meta_tools/__init__.py)
元工具的存放目录

## 注意：
1. [meta_tool.schema](./meta_tools/interface/meta_tool.py)是共享属性，重复实例化会重复注册
2. 元工具通过各模块的`__init__`加入到schema中
