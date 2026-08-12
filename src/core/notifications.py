import uuid

import warehouse_stub as ws
from clock import Clock

SENDER_ADDRESS = "agent@setuhaul.example"


class PostgresWarehouseStore:
    """WarehouseStore backed by operational_messages / pending_warehouse_replies.
    No in-memory state (I13) — every call is a plain query against con."""

    def __init__(self, con, clock: Clock):
        self._con = con
        self._clock = clock

    def record_outbound(self, msg: ws.OutboundMessage, status: str) -> None:
        self._con.execute(
            """
            INSERT INTO operational_messages
                (operational_message_id, shipment_id, appointment_id, channel,
                 sender_address, recipient_address, subject, message_body,
                 sent_at, delivery_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                msg.operational_message_id,
                msg.shipment_id,
                msg.appointment_id,
                msg.channel,
                msg.sender_address,
                msg.recipient_address,
                msg.subject,
                msg.body,
                self._clock.now(),
                status,
            ),
        )

    def schedule_pending_reply(self, reply: ws.PendingReply) -> None:
        self._con.execute(
            """
            INSERT INTO pending_warehouse_replies
                (reply_id, appointment_id, in_reply_to, affirmative, deliver_after)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                reply.reply_id,
                reply.appointment_id,
                reply.in_reply_to,
                reply.affirmative,
                reply.deliver_after,
            ),
        )

    def due_replies(self, now) -> list[ws.PendingReply]:
        rows = self._con.execute(
            """
            SELECT reply_id, appointment_id, in_reply_to, affirmative, deliver_after
            FROM pending_warehouse_replies
            WHERE delivered_at IS NULL AND deliver_after <= %s
            """,
            (now,),
        ).fetchall()
        return [
            ws.PendingReply(
                reply_id=r[0],
                appointment_id=r[1],
                in_reply_to=r[2],
                affirmative=r[3],
                deliver_after=r[4],
            )
            for r in rows
        ]

    def mark_reply_delivered(self, reply_id: str, now) -> None:
        self._con.execute(
            "UPDATE pending_warehouse_replies SET delivered_at = %s WHERE reply_id = %s",
            (now, reply_id),
        )

    def record_inbound_reply(self, reply: ws.PendingReply, body: str, now) -> None:
        # I6: only REPLIED + affirmative may confirm. affirmative is carried
        # by the pending_warehouse_replies row itself (reply.affirmative);
        # delivery_status on the original outbound message becomes REPLIED
        # per operational_messages.delivery_status's documented meaning.
        self._con.execute(
            "UPDATE operational_messages SET delivery_status = 'REPLIED' WHERE operational_message_id = %s",
            (reply.in_reply_to,),
        )


def notify_warehouse(
    con, appointment_id: str, shipment_id: str, dock_id: str, clock: Clock, channel: str = "EMAIL"
) -> bool:
    """Sends the booking request to the facility's warehouse coordinator.
    channel defaults to EMAIL; the pending-confirmation sweep retries on
    SMS per the channel ladder (§7) if EMAIL fails or times out. Returns
    True if the message left our system (SENT/DELIVERED/QUEUED), False if
    it failed outright (e.g. no email on file for the contact)."""
    facility_row = con.execute(
        """
        SELECT f.facility_id, d.dock_code FROM docks d
        JOIN facilities f ON f.facility_id = d.facility_id
        WHERE d.dock_id = %s
        """,
        (dock_id,),
    ).fetchone()
    facility_id, dock_code = facility_row

    address_column = "email" if channel == "EMAIL" else "phone"
    contact = con.execute(
        f"""
        SELECT {address_column} FROM facility_contacts
        WHERE facility_id = %s AND contact_role = 'WAREHOUSE_COORDINATOR' AND active_flag = TRUE
        LIMIT 1
        """,
        (facility_id,),
    ).fetchone()
    recipient = contact[0] if contact else None

    msg = ws.OutboundMessage(
        operational_message_id=f"OM-{uuid.uuid4().hex[:12]}",
        shipment_id=shipment_id,
        appointment_id=appointment_id,
        channel=channel,
        sender_address=SENDER_ADDRESS,
        recipient_address=recipient,
        subject=f"Slot request for {shipment_id}" if channel == "EMAIL" else None,
        body=f"Requesting confirmation for {appointment_id} at dock {dock_code}.",
    )

    warehouse = ws.StandInWarehouse(PostgresWarehouseStore(con, clock), clock)
    result = warehouse.send(msg)
    return result.status != "FAILED"
