"""
Stand-in warehouse.

The seeded dataset has no inbound messages - all five operational_messages are
outbound with reply_to_message_id NULL, and chat has only DRIVER and AGENT
senders. Nothing can produce the REPLIED status that decision 4 requires
before an appointment may become CONFIRMED, so without this module a booking
reaches PENDING_CONFIRMATION and goes no further.

Deliberately a puppet, not a simulation. It does two things:
  1. accepts an outbound message and reports a delivery_status;
  2. optionally schedules an inbound reply for later delivery.

It NEVER confirms an appointment itself. It only produces the reply; the
operational core reads REPLIED + affirmative and decides. Keeping that
boundary intact is the whole point - otherwise the demo proves nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

from clock import Clock

DeliveryStatus = Literal["QUEUED", "SENT", "DELIVERED", "FAILED", "REPLIED"]

WarehouseMode = Literal[
    "CONFIRM",       # replies affirmatively after reply_delay_seconds
    "DECLINE",       # replies negatively - slot released, driver re-offered
    "SILENT",        # delivers but never replies - exercises section 10.2 expiry
    "SEND_FAILURE",  # send fails outright - the OM004 escalation case
    "QUEUED_STUCK",  # stuck in the outbox - the OM002 case, do not run the clock
]


@dataclass(frozen=True)
class OutboundMessage:
    operational_message_id: str
    shipment_id: str
    appointment_id: str
    channel: Literal["EMAIL", "SMS", "WHATSAPP", "INTERNAL"]
    sender_address: str
    recipient_address: str | None
    subject: str | None
    body: str


@dataclass(frozen=True)
class SendResult:
    status: DeliveryStatus
    failure_reason: str | None = None
    reply_due_at: datetime | None = None


@dataclass(frozen=True)
class PendingReply:
    """
    Scheduled replies are ROWS, not timers. Serverless functions do not survive
    between invocations, so a background timer would simply never fire. A cron
    tick - or an explicit demo step - calls deliver_due_replies().

        CREATE TABLE pending_warehouse_replies (
            reply_id       TEXT PRIMARY KEY,
            appointment_id TEXT NOT NULL,
            in_reply_to    TEXT NOT NULL,
            affirmative    BOOLEAN NOT NULL,
            deliver_after  TIMESTAMPTZ NOT NULL,
            delivered_at   TIMESTAMPTZ
        );
    """
    reply_id: str
    appointment_id: str
    in_reply_to: str
    affirmative: bool
    deliver_after: datetime


@dataclass
class WarehouseConfig:
    mode: WarehouseMode = "CONFIRM"
    reply_delay_seconds: int = 45
    # Per-appointment overrides, so one demo run can show several branches.
    overrides: dict[str, WarehouseMode] = field(default_factory=dict)


class WarehouseStore(Protocol):
    def record_outbound(self, msg: OutboundMessage, status: DeliveryStatus) -> None: ...
    def schedule_pending_reply(self, reply: PendingReply) -> None: ...
    def due_replies(self, now: datetime) -> list[PendingReply]: ...
    def mark_reply_delivered(self, reply_id: str, now: datetime) -> None: ...
    def record_inbound_reply(self, reply: PendingReply, body: str, now: datetime) -> None: ...


class StandInWarehouse:
    def __init__(self, store: WarehouseStore, clock: Clock,
                 config: WarehouseConfig | None = None):
        self._store = store
        self._clock = clock
        self._config = config or WarehouseConfig()

    def set_mode(self, mode: WarehouseMode) -> None:
        self._config = replace(self._config, mode=mode)

    def set_override(self, appointment_id: str, mode: WarehouseMode) -> None:
        self._config.overrides[appointment_id] = mode

    def _mode_for(self, appointment_id: str) -> WarehouseMode:
        return self._config.overrides.get(appointment_id, self._config.mode)

    def send(self, msg: OutboundMessage) -> SendResult:
        # Data-driven failure, independent of mode. CON005 (Gurgaon night
        # shift) is a vacant role with a NULL email and a valid phone - an
        # EMAIL to a missing address must fail here exactly as OM004 did in
        # the seed, or the channel-ladder logic in section 10.2 is never
        # exercised.
        if msg.channel == "EMAIL" and not msg.recipient_address:
            self._store.record_outbound(msg, "FAILED")
            return SendResult("FAILED", failure_reason="NO_EMAIL_ADDRESS_FOR_CONTACT")

        mode = self._mode_for(msg.appointment_id)

        if mode == "SEND_FAILURE":
            self._store.record_outbound(msg, "FAILED")
            return SendResult("FAILED", failure_reason="SMTP_PERMANENT_FAILURE")

        if mode == "QUEUED_STUCK":
            # Never leaves the outbox. Waiting on this is pointless - the whole
            # reason decision 4 keys off delivery status rather than a timer.
            self._store.record_outbound(msg, "QUEUED")
            return SendResult("QUEUED")

        self._store.record_outbound(msg, "DELIVERED")

        if mode == "SILENT":
            return SendResult("DELIVERED")

        deliver_after = self._clock.now() + timedelta(seconds=self._config.reply_delay_seconds)
        self._store.schedule_pending_reply(PendingReply(
            reply_id=f"RPL-{msg.operational_message_id}",
            appointment_id=msg.appointment_id,
            in_reply_to=msg.operational_message_id,
            affirmative=(mode == "CONFIRM"),
            deliver_after=deliver_after,
        ))
        return SendResult("DELIVERED", reply_due_at=deliver_after)

    def deliver_due_replies(self) -> list[tuple[str, bool]]:
        """
        Called by the cron tick, or directly as a demo step. Writes inbound
        replies; does NOT touch appointment status. Returns (appointment_id,
        affirmative) so the caller can run them through the real confirmation
        path.
        """
        now = self._clock.now()
        landed: list[tuple[str, bool]] = []
        for reply in self._store.due_replies(now):
            body = (
                f"Confirmed. {reply.appointment_id} is accepted for the requested dock window."
                if reply.affirmative else
                f"Cannot accept {reply.appointment_id} for that window. "
                f"Labour is committed elsewhere."
            )
            self._store.record_inbound_reply(reply, body, now)
            self._store.mark_reply_delivered(reply.reply_id, now)
            landed.append((reply.appointment_id, reply.affirmative))
        return landed


# --------------------------------------------------------------------------
# Demo wiring. Each line is one branch of decision 4, off a single stand-in.
#
#   warehouse.set_mode("CONFIRM")                      # happy path
#   warehouse.set_override("APT-A", "DECLINE")         # slot released
#   warehouse.set_override("APT-B", "SILENT")          # 20-min expiry fires
#   warehouse.set_override("APT-C", "SEND_FAILURE")    # OM004 escalation
#   warehouse.set_override("APT-D", "QUEUED_STUCK")    # OM002 escalation
#
# With a SimulatedClock, advance rather than wait:
#
#   clock.advance_minutes(1);  warehouse.deliver_due_replies()   # confirms
#   clock.advance_minutes(21)                                    # expiry fires
#
# One caution for the live demo: an affirmative reply is not permission to
# confirm blindly. The core must re-validate feasibility first, because the
# slot may have been lost to a race while the reply was in flight. A warehouse
# saying yes to something no longer possible is a real operational case and
# must escalate, not confirm.
# --------------------------------------------------------------------------
