import uuid

import pytest
from langchain_core.callbacks.base import BaseCallbackHandler

from src.agent import handle_message
from src.reset_demo import reset_demo


class ToolCallRecorder(BaseCallbackHandler):
    def __init__(self):
        self.tool_calls: list[str] = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls.append(serialized.get("name", "unknown"))


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()


def test_drv004_ambiguous_message_asks_no_tool_call():
    """DRV004 has two active shipments (SHP1004, SHP1020). A message that
    doesn't name either must produce a clarification question and call no
    tool at all (§9, done-when for Step 12)."""
    from src.agent import build_agent_with_history

    thread_id = "DRV004-2026-08-04-test"
    recorder = ToolCallRecorder()
    agent = build_agent_with_history()
    response = agent.invoke(
        {"input": "Hey, I'm running late, traffic is bad."},
        config={
            "configurable": {
                # message_store.session_id is UUID-typed (langchain_postgres);
                # thread_id stays human-readable for chat_threads/ownership.
                "session_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, thread_id)),
                "driver_id": "DRV004",
                "thread_id": thread_id,
            },
            "callbacks": [recorder],
        },
    )
    output = response["output"]

    assert recorder.tool_calls == [], f"expected no tool calls, got {recorder.tool_calls}"
    assert "?" in output, f"expected a clarifying question, got: {output!r}"


def test_smoke_handle_message_returns_text():
    reply = handle_message("DRV004", "2026-08-04-smoke", "hello")
    assert isinstance(reply, str)
    assert len(reply) > 0
