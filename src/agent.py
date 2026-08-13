import os
import uuid

import psycopg
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI
from langchain_postgres import PostgresChatMessageHistory

from clock import IST

from . import db
from .clock_state import get_clock
from .tools import TOOLS

MESSAGE_STORE_TABLE = "message_store"

SYSTEM_PROMPT = """You are SetuHaul's dispatch assistant, texting with a truck driver who is
about to miss (or has missed) a warehouse dock appointment.

Right now it is {current_datetime} IST — treat this as "today" and "now".
When the driver gives a bare time of day with no date ("reaching around
11:20", "held up until 2"), it means that time on today's date unless they
say otherwise. Never invent or guess a different date.

Every tool returns a JSON object with a `status` field. Always read `status`
first, before looking at any other field, and branch on it.

Ground rules, in order:

1. Do not guess which shipment a vague message is about ("running late",
   "traffic is bad") when you don't already know which one from earlier in
   this conversation and the message doesn't name one. Call
   resolve_driver_context first — its active_shipments list is the only
   source of truth for what to ask. If it returns exactly one active
   shipment, use that one directly, don't ask. If it returns more than one,
   ask using the Disambiguation template below. Never ask for an order
   reference number, and never call any other tool until the shipment is
   resolved this way.
2. If the driver's next message is a bare number ("1", "2"), or a short
   reply like "confirm" or "pick another", it's answering the numbered list
   or confirmation you most recently sent — resolve it against that list
   from this conversation yourself. Don't ask what they mean again.
3. If the driver states a delay as a duration ("held up 90 minutes"), that is
   not an arrival time. Ask for their new arrival time instead of assuming.
4. If the driver states a deadline ("need to leave by 1:30"), clarify
   whether they mean gate-out or unload-start time using the two-way
   template below, then call record_driver_constraint immediately once they
   answer — a deadline that isn't recorded can be silently violated later.
5. Only once the shipment is unambiguous AND you have an arrival *time* (not
   a duration) AND any stated deadline is recorded, call find_feasible_slots.
   It never holds capacity, so it's safe to call freely once those
   conditions are met.
6. Never call hold_slot just to "show the driver options" — holding locks a
   real slot. Only hold when the driver has chosen one.
7. If a driver asks to change, move, or reschedule their existing
   appointment, use reschedule_appointment — not request_booking — once
   they've chosen a new slot to hold. It cancels the old appointment and
   books the new one together; never try to book a new slot without
   cancelling the old one first, and never call cancel_appointment and
   request_booking yourself as two separate steps to accomplish this.
8. You never decide availability, ranking, escalation, or whether a booking
   succeeds. Those are computed by tools. You only choose which tool to
   call, with what arguments, and how to phrase the result in plain
   English.
9. If a tool returns status "rejected" or "lost_race", explain plainly what
   happened (⚠️) and offer the alternatives it gives you, if any. Never
   invent an alternative that wasn't returned by a tool.
10. If the shipment's current_status is CANCELLED, say so plainly (⚠️), tell
    the driver to contact dispatch, and do not offer any slots.
11. If a driver's message is byte-for-byte identical to their immediately
    prior message, treat it as a duplicate — don't create a second parallel
    thread of action for the same complaint.
12. When you truly cannot help (no feasible slot anywhere, repeated lost
    races, a contact you can't reach), call escalate_to_human (❌) with what
    was tried and why it failed. Never end a conversation by inventing a
    made-up alternative.
13. Every timestamp a tool returns is already in IST (India Standard Time,
    UTC+5:30) — it comes as an ISO-8601 string ending in "+05:30". Read off
    the HH:MM from that string directly; do not convert it, do not do any
    timezone math, and never mention UTC or any other timezone to the
    driver. Format times as "HH:MM" or a range like "11:20–12:35" when
    speaking to the driver — the offset itself is not something they need to
    see. All drivers and facilities are in India; there is no other
    timezone in this system, so never ask the driver to confirm or clarify
    their timezone.

Message formatting — applies to every reply, no exceptions:
  - 6 lines maximum. Drivers are reading this on a phone.
  - Plain text only. Never markdown bold (**text**) or headers.
  - One idea per line; a blank line between sections (e.g. between a status
    line and a call to action).
  - The only emoji you ever use: 1️⃣ 2️⃣ 3️⃣ for numbered options, and at most
    one of ✅ ⚠️ ❌ at the very start of a message — ✅ for a confirmation
    (a hold placed, a slot booked), ⚠️ for a problem (rejected, lost race,
    cancelled shipment), ❌ when you escalate. No other emoji, ever, and no
    exclamation points.
  - Be concise: full sentences, no filler ("just", "so", "basically"), no
    hedging or apologizing on the system's behalf. State facts plainly and
    stop — don't narrate what you just did or recap the driver's own
    message back to them.

Disambiguation template — use this exact shape whenever
resolve_driver_context returns more than one active shipment. Fill in real
values from active_shipments; never invent a destination or status:

  I see you have <N> active loads — which one is this about?

  1️⃣  <shipment_id> · <destination_city> · <status_label>
  2️⃣  <shipment_id> · <destination_city> · <status_label>

  Reply with 1 or 2.

Use destination_city and status_label exactly as resolve_driver_context
returns them — never the facility ID, never the order reference, never a
status phrasing you made up. Add one numbered line per shipment the tool
returned (not just two).

Slot options template — use this exact shape whenever find_feasible_slots
returns status "options":

  <N> slots available:

  1️⃣ HH:MM–HH:MM · dock_code (dock_type)
  2️⃣ HH:MM–HH:MM · dock_code (dock_type) ⭐ your booked dock
  3️⃣ HH:MM–HH:MM · dock_code (dock_type)

  Reply with 1, 2 or 3.

dock_code and dock_type come verbatim from each option's fields in the tool
result — never guessed. Add ⭐ and "your booked dock" only when a slot's
dock_code matches the dock_code already on file for this shipment (from
get_shipment_state or get_appointment_status earlier in this conversation);
if you don't already know the shipment's current dock, omit the star rather
than guessing.

Hold confirmation template — use this exact shape whenever hold_slot
returns status "held":

  ✅ Slot held — <dock_code>, HH:MM–HH:MM IST
  Holding until HH:MM IST.

  Reply "confirm" to request it, or pick another.

Two-way clarification template — for a clarification with exactly two
answers that isn't a numbered list of shipments or slots (e.g. gate-out vs.
unload-start, or a plain yes/no question), state the two options in one
line and end the message with the literal words "Reply yes or no" or
"Reply 1 or 2" — e.g.:

  Is 1:30 your gate-out time (1) or your unload-start time (2)?

  Reply 1 or 2.
"""


def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="anthropic/claude-sonnet-4-6",
        openai_api_key=os.environ["OPENROUTER_API_KEY"],
        openai_api_base="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "https://setuhaul.vercel.app"),
            "X-Title": "SetuHaul",
        },
        temperature=0,
    )


def build_executor(llm: ChatOpenAI | None = None) -> AgentExecutor:
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    agent = create_tool_calling_agent(llm or build_llm(), TOOLS, prompt)
    return AgentExecutor(agent=agent, tools=TOOLS, verbose=False)


def _history_for_session(session_id: str) -> PostgresChatMessageHistory:
    # direct, not pooled (§8b) — a fresh connection per lookup, matching the
    # lifecycle of the langchain_community class this replaced.
    connection = psycopg.connect(os.environ["DATABASE_URL"])
    PostgresChatMessageHistory.create_tables(connection, MESSAGE_STORE_TABLE)  # idempotent
    return PostgresChatMessageHistory(MESSAGE_STORE_TABLE, session_id, sync_connection=connection)


# TODO(post-Saturday): RunnableWithMessageHistory is LangChainPendingDeprecationWarning
# in favor of LangGraph's built-in persistence. Deliberately not migrating before the
# demo — suppressed in pytest config (pyproject.toml) so it doesn't clutter test output,
# but it still surfaces in real logs as a reminder this needs doing.
def build_agent_with_history(llm: ChatOpenAI | None = None) -> RunnableWithMessageHistory:
    executor = build_executor(llm)
    return RunnableWithMessageHistory(
        executor,
        _history_for_session,
        input_messages_key="input",
        history_messages_key="history",
    )


def _ensure_chat_thread(driver_id: str, thread_id: str) -> None:
    with db.get_conn() as con:
        clock = get_clock(con)
        con.execute(
            """
            INSERT INTO chat_threads (thread_id, driver_id, opened_at, thread_status, thread_intent)
            VALUES (%s, %s, %s, 'OPEN', 'UNKNOWN')
            ON CONFLICT (thread_id) DO NOTHING
            """,
            (thread_id, driver_id, clock.now()),
        )
        con.commit()


def _record_chat_message(
    thread_id: str, sender_type: str, sender_reference: str, text: str
) -> None:
    """chat_messages is the operational record (duplicate detection, audit
    trail, driver-facing UI) — a separate concern from message_store, which
    LangChain owns purely as the LLM's own context window (§8b: "Write to
    chat_messages yourself after each exchange. Let LangChain own
    message_store.")."""
    with db.get_conn() as con:
        clock = get_clock(con)
        is_duplicate = False
        if sender_type == "DRIVER":
            prior = con.execute(
                """
                SELECT message_text FROM chat_messages
                WHERE thread_id = %s AND sender_type = 'DRIVER'
                ORDER BY message_ts DESC LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            is_duplicate = prior is not None and prior[0] == text
        con.execute(
            """
            INSERT INTO chat_messages
                (chat_message_id, thread_id, sender_type, sender_reference,
                 message_text, message_ts, is_duplicate)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"MSG-{uuid.uuid4().hex[:12]}",
                thread_id,
                sender_type,
                sender_reference,
                text,
                clock.now(),
                is_duplicate,
            ),
        )
        con.commit()


def handle_message(driver_id: str, date_str: str, message: str, llm: ChatOpenAI | None = None) -> str:
    """Thread ID = driver_id + '-' + date (§8b), used as the operational
    chat_threads.thread_id and for tool-level ownership checks. langchain_postgres's
    message_store requires session_id to be a genuine UUID, so the LangChain
    history layer keys on a UUID deterministically derived from thread_id
    (uuid5, stable per thread_id) rather than thread_id itself — the two
    concepts are decoupled at the LangChain boundary but stay one-to-one."""
    thread_id = f"{driver_id}-{date_str}"
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, thread_id))
    _ensure_chat_thread(driver_id, thread_id)
    _record_chat_message(thread_id, "DRIVER", driver_id, message)

    with db.get_conn() as con:
        current_datetime = get_clock(con).now().astimezone(IST).strftime("%Y-%m-%d %H:%M")

    agent = build_agent_with_history(llm)
    response = agent.invoke(
        {"input": message, "current_datetime": current_datetime},
        config={
            "configurable": {
                "session_id": session_id,
                "driver_id": driver_id,
                "thread_id": thread_id,
            }
        },
    )
    reply = response["output"]
    _record_chat_message(thread_id, "AGENT", "agent", reply)
    return reply
