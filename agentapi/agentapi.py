import json
from typing import cast, Literal,Any

from openai import AsyncOpenAI as openai
from openai.types.chat import ChatCompletionMessageParam as ccmp, ChatCompletionMessage as ccm, ChatCompletionToolParam as cctp
from openai.lib.streaming.chat import AsyncChatCompletionStreamManager
from .meta_tools import get_schemas as getschemas
from .meta_tools.tool_run import tool as tool_runner

role = Literal["assistant","developer","function","system","tool","user","latest_reminder"]

def _from_message(message: ccm) -> ccmp:
    msg: dict[str, Any] = {"role": "assistant"}
    if message.content is not None:
        msg["content"] = message.content
    if message.refusal is not None:
        msg["refusal"] = message.refusal
    if message.tool_calls:
        msg["tool_calls"] = [tc.model_dump(exclude_none=False) for tc in message.tool_calls]
    return cast(ccmp, msg)

class agent_api:
    __model:openai
    __model_name:str
    __stopped:bool

    def __init__(self, openai:openai, model_name: str):
        self.__model = openai
        self.__model_name = model_name
        self.__stopped = False

    def stop(self) -> None:
        self.__stopped = True

    def resume(self) -> None:
        self.__stopped = False

    @property
    def stopped(self) -> bool:
        return self.__stopped

    @property
    def model_name(self) -> str:
        return self.__model_name

    @model_name.setter
    def model_name(self, name: str) -> None:
        self.__model_name = name
    async def chat(self, usrmsg: list[ccmp], reasioning_effort:Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]="medium", extra:dict[Any,Any]|None=None)->ccmp:
        res = await self.__model.chat.completions.create(
            model=self.__model_name,
            messages=usrmsg,
            tools=self.get_schemas(),
            reasoning_effort=reasioning_effort,
            extra_body=extra
        )
        return _from_message(res.choices[0].message)

    async def chat_stream(self, usrmsg: list[ccmp],reasoning_effort:Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]="medium", extra:dict[Any,Any]|None=None) -> AsyncChatCompletionStreamManager:
        return self.__model.chat.completions.stream(
            model=self.__model_name,
            messages=usrmsg,
            extra_body={**(extra or {}), "tools": self.get_schemas()},
            reasoning_effort=reasoning_effort,
            stream_options={"include_usage": True}
        )

    def tool(self, message: ccmp) -> list[ccmp]:
        calls = list(message.get("tool_calls") or [])
        results: list[ccmp] = []
        for call in calls:
            if self.__stopped:
                results.append(cast(ccmp, {"role": "tool", "tool_call_id": call["id"], "content": "对话已中止，工具未执行"}))
                continue
            if call["type"] != "function":
                content = f"工具调用失败：不支持的工具调用类型 {call['type']}"
            else:
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                    result = tool_runner.run(tool_name=call["function"]["name"], arguments=arguments)
                except json.JSONDecodeError as e:
                    content = f"工具调用失败：参数不是合法 JSON（{e}）"
                except Exception as e:
                    content = f"工具调用失败：{type(e).__name__}: {e}"
                else:
                    content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True, default=str)
            results.append(cast(ccmp, {"role": "tool", "tool_call_id": call["id"], "content": content}))
        return results

    def get_schemas(self) -> list[cctp]:
        return cast(list[cctp],getschemas())

    def deny(self, message: ccmp, reason: str = "用户拒绝工具调用") -> list[ccmp]:
        return [
            cast(ccmp, {"role": "tool", "tool_call_id": call["id"], "content": reason})
            for call in (message.get("tool_calls") or [])
        ]

    async def models(self) -> list[str]:
        res = await self.__model.models.list()
        return [m.id for m in res.data]

    async def balance(self) -> dict[str, Any]:
        return cast(dict[str, Any], await self.__model.get("/user/balance", cast_to=dict))
