"""单智能体工具执行图。"""

from langgraph.graph import END, StateGraph

from src.agent.nodes import create_llm, execute_node, observe_node, plan_node, profile_policy
from src.agent.state import AgentState
from src.config import Config


def should_continue(state: AgentState) -> str:
    if (
        state.final_answer is not None
        or state.current_step >= state.max_steps
        or not state.should_continue
    ):
        return END
    return "execute"


def create_agent_graph(base_url="http://localhost:11434/v1", model="qwen3.5:4b"):
    profile = Config.from_env().agent_profile
    profile_policy(profile)
    llm = create_llm(base_url, model)

    async def plan(state):
        return await plan_node(state, llm, profile)

    async def execute(state):
        return await execute_node(state, profile)

    workflow = StateGraph(AgentState)
    workflow.add_node("plan", plan)
    workflow.add_node("execute", execute)
    workflow.add_node("observe", observe_node)
    workflow.set_entry_point("plan")
    workflow.add_conditional_edges("plan", should_continue, {"execute": "execute", END: END})
    workflow.add_edge("execute", "observe")
    workflow.add_conditional_edges("observe", should_continue, {"execute": "plan", END: END})
    return workflow.compile()
