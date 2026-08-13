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


def test_drv004_ambiguous_message_disambiguates_with_real_data():
    """DRV004 has two active shipments (SHP1004, SHP1020). A message that
    doesn't name either must call resolve_driver_context to get real
    shipment data, then ask a numbered disambiguation question built from
    it — never a bare request for an order reference number."""
    from src.agent import build_agent_with_history

    thread_id = "DRV004-2026-08-04-test"
    recorder = ToolCallRecorder()
    agent = build_agent_with_history()
    response = agent.invoke(
        {"input": "Hey, I'm running late, traffic is bad.", "current_datetime": "2026-08-04 04:30"},
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

    assert recorder.tool_calls == ["resolve_driver_context"], (
        f"expected exactly one resolve_driver_context call, got {recorder.tool_calls}"
    )
    assert "SHP1004" in output and "SHP1020" in output, f"expected both shipment IDs, got: {output!r}"
    assert "1️⃣" in output and "2️⃣" in output, f"expected numbered options, got: {output!r}"
    assert "**" not in output, f"expected no markdown bold, got: {output!r}"


def test_smoke_handle_message_returns_text():
    reply = handle_message("DRV004", "2026-08-04-smoke", "hello")
    assert isinstance(reply, str)
    assert len(reply) > 0
