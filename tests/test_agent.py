import uuid

import pytest
from langchain_core.callbacks.base import BaseCallbackHandler

from src import db
from src.agent import handle_message
from src.reset_demo import reset_demo

# message_store is langchain_postgres's own table — reset_demo() doesn't
# know about it (it's not part of the application schema), so conversation
# history for these fixed test threads accumulates across every past test
# run unless cleared explicitly. Without this, repeated runs see a longer
# and longer prior conversation, which can change the LLM's behavior (e.g.
# shifting from asking a fresh question to reiterating one already "asked"
# many times in the accumulated history).
TEST_THREAD_IDS = ["DRV004-2026-08-04-test", "DRV004-2026-08-04-smoke"]


class ToolCallRecorder(BaseCallbackHandler):
    def __init__(self):
        self.tool_calls: list[str] = []

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls.append(serialized.get("name", "unknown"))


def _clear_message_store():
    session_ids = [str(uuid.uuid5(uuid.NAMESPACE_DNS, t)) for t in TEST_THREAD_IDS]
    with db.get_conn() as con:
        con.execute("DELETE FROM message_store WHERE session_id = ANY(%s)", (session_ids,))
        con.commit()


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    reset_demo()
    _clear_message_store()


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
