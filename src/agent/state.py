"""Agent state definition."""

from collections.abc import Sequence
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from src.tools.rag import KnowledgeBase


class AgentState(BaseModel):
    """State for the agent graph."""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    knowledge_base: KnowledgeBase = "daily"
    warnings: list[str] = Field(default_factory=list)
    current_step: int = Field(default=0, description="Current execution step")
    max_steps: int = Field(default=10, description="Maximum steps allowed")
    tool_calls: list[dict] = Field(default_factory=list, description="Tools to execute")
    tool_results: list[dict] = Field(default_factory=list, description="Tool execution results")
    final_answer: str | None = Field(default=None, description="Final answer to user")
    should_continue: bool = Field(default=True, description="Whether to continue execution")
