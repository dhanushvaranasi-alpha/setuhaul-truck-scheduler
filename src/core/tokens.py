import hashlib
import hmac
import os

from ..models import OptionToken


def _secret() -> bytes:
    return os.environ["OPTION_TOKEN_SECRET"].encode()


def compute_state_hash(con, slot_ids: list[str]) -> str:
    """A cheap fingerprint of the slots' occupancy state at issuance time, so
    hold_slot can fail fast with LOST_RACE if it changed — the EXCLUDE
    constraint on slot_holds is still the actual guard (I2); this is just an
    earlier, friendlier check."""
    rows = con.execute(
        """
        SELECT sl.slot_id, sl.slot_status,
               EXISTS (SELECT 1 FROM slot_holds h WHERE h.slot_id = sl.slot_id AND h.hold_status = 'ACTIVE') AS held,
               EXISTS (
                   SELECT 1 FROM appointment_slot_allocations al
                   JOIN appointments a ON a.appointment_id = al.appointment_id
                   WHERE al.slot_id = sl.slot_id AND a.is_current
                     AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
               ) AS occupied
        FROM appointment_slots sl
        WHERE sl.slot_id = ANY(%s)
        ORDER BY sl.slot_id
        """,
        (slot_ids,),
    ).fetchall()
    fingerprint = "|".join(f"{r[0]}:{r[1]}:{r[2]}:{r[3]}" for r in rows)
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def issue_option_token(
    con, span_slot_ids: list[str], shipment_id: str, issued_at: str
) -> str:
    state_hash = compute_state_hash(con, span_slot_ids)
    payload = "|".join(span_slot_ids) + f"|{shipment_id}|{state_hash}|{issued_at}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    token = OptionToken(
        span_slot_ids=span_slot_ids,
        shipment_id=shipment_id,
        state_hash=state_hash,
        issued_at=issued_at,
        signature=signature,
    )
    return token.model_dump_json()


def verify_option_token(con, token_json: str, shipment_id: str) -> OptionToken | None:
    """Returns the verified token, or None if the signature is invalid, the
    shipment doesn't match, or the state has changed since issuance."""
    token = OptionToken.model_validate_json(token_json)
    if token.shipment_id != shipment_id:
        return None
    payload = (
        "|".join(token.span_slot_ids)
        + f"|{token.shipment_id}|{token.state_hash}|{token.issued_at}"
    )
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, token.signature):
        return None
    if compute_state_hash(con, token.span_slot_ids) != token.state_hash:
        return None
    return token
