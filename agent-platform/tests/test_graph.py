import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent import graph as graph_module
from src.agent.nodes import execute_node, plan_node
from src.agent.state import AgentState
from src.tools.base import BaseTool, registry


class FakeLLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []
        self.schemas = None

    def bind_tools(self, schemas):
        self.schemas = schemas
        return self

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return next(self.replies)


class SampleTool(BaseTool):
    name = "sample"
    description = "sample"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }

    async def execute(self, value):
        return {"success": True, "rows": [{"value": value}]}


async def test_real_graph_calls_native_tool_then_answers(monkeypatch):
    monkeypatch.setattr(registry, "_tools", {"sample": SampleTool()})
    llm = FakeLLM(
        [
            AIMessage(
                content="", tool_calls=[{"id": "call-1", "name": "sample", "args": {"value": 42}}]
            ),
            AIMessage(content="结果是42"),
        ]
    )
    monkeypatch.setattr(graph_module, "create_llm", lambda *args: llm)
    result = await graph_module.create_agent_graph().ainvoke(
        AgentState(messages=[HumanMessage(content="测试")])
    )
    assert result["final_answer"] == "结果是42"
    assert result["current_step"] == 2
    assert isinstance(llm.messages[-1][-1], ToolMessage)
    assert llm.messages[-1][-1].tool_call_id == "call-1"
    assert '"value": 42' in llm.messages[-1][-1].content
    assert llm.schemas[0]["function"]["name"] == "sample"


async def test_final_answer_does_not_execute_tool_node(monkeypatch):
    llm = FakeLLM([AIMessage(content="你好")])
    monkeypatch.setattr(graph_module, "create_llm", lambda *args: llm)
    result = await graph_module.create_agent_graph().ainvoke(
        AgentState(messages=[HumanMessage(content="你好")])
    )
    assert result["final_answer"] == "你好"
    assert result["tool_results"] == []
    assert len(result["messages"]) == 2


@pytest.mark.parametrize(
    "reply",
    [
        AIMessage(content=""),
        AIMessage(content="未完成的回答", response_metadata={"finish_reason": "length"}),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "sample", "args": "{", "id": "invalid", "error": "bad json"}
            ],
        ),
    ],
)
async def test_invalid_model_response_is_reported(reply):
    with pytest.raises(ValueError):
        await plan_node(AgentState(messages=[]), FakeLLM([reply]))


@pytest.mark.parametrize("parameters", [{"value": "bad"}, {"value": 1, "extra": True}, {}])
async def test_tool_parameters_are_validated_before_execution(monkeypatch, parameters):
    monkeypatch.setattr(registry, "_tools", {"sample": SampleTool()})
    result = await execute_node(
        AgentState(
            messages=[], tool_calls=[{"tool": "sample", "id": "call-2", "parameters": parameters}]
        )
    )
    assert not result["tool_results"][0]["success"]
    assert result["messages"][0].tool_call_id == "call-2"


async def test_openai_compatible_client_with_real_graph(monkeypatch):
    import json

    import httpx
    from langchain_openai import ChatOpenAI

    payloads = []
    monkeypatch.setattr(registry, "_tools", {"sample": SampleTool()})

    def respond(request):
        payloads.append(json.loads(request.content))
        message = {"role": "assistant", "content": "原生调用成功"}
        if len(payloads) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "native-1",
                        "type": "function",
                        "function": {"name": "sample", "arguments": '{"value":42}'},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if len(payloads) == 1 else "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        llm = ChatOpenAI(
            model="test", api_key="test", base_url="http://mock/v1", http_async_client=client
        )
        monkeypatch.setattr(graph_module, "create_llm", lambda *args: llm)
        result = await graph_module.create_agent_graph().ainvoke(
            AgentState(messages=[HumanMessage(content="测试")])
        )
    assert result["final_answer"] == "原生调用成功"
    assert payloads[0]["tools"][0]["function"]["name"] == "sample"
    assert payloads[1]["messages"][-1]["role"] == "tool"
    assert payloads[1]["messages"][-1]["tool_call_id"] == "native-1"
