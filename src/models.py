from typing import Literal, Union

from pydantic import BaseModel

# --------------------------------------------------------------------------
# Tool results — every tool returns one of these. The LLM reads `status`
# first (always a Literal) and branches on it.
# --------------------------------------------------------------------------


class FeasibleSpan(BaseModel):
    option_token: str
    dock_id: str
    dock_code: str
    dock_type: str
    span_start: str  # ISO-8601 with +05:30
    span_end: str


class Booked(BaseModel):
    status: Literal["booked"]
    appointment_id: str
    dock_code: str
    span_start: str  # ISO-8601 with +05:30
    span_end: str
    warehouse_notified: bool


class Rejected(BaseModel):
    status: Literal["rejected"]
    reason: str  # one of the reason codes from §3
    detail: str | None = None  # plain English for the LLM to phrase to the driver


class LostRace(BaseModel):
    status: Literal["lost_race"]
    message: str  # "that slot went 30 seconds ago"
    alternatives: list[FeasibleSpan]  # next feasible options already fetched


class Escalated(BaseModel):
    status: Literal["escalated"]
    escalation_id: str
    reason_code: str
    assigned_to: str
    update_by: str  # committed time to tell the driver


class Options(BaseModel):
    status: Literal["options"]
    spans: list[FeasibleSpan]  # up to 3
    searched_until: str  # how far the ladder searched


class HoldConfirmed(BaseModel):
    status: Literal["held"]
    hold_group_id: str
    expires_at: str  # absolute ISO-8601 — tell the driver this time
    ttl_seconds: int
    contention_ratio: float


# §8a names 6 result types, one per booking-critical tool (find_feasible_slots
# -> Options, hold_slot -> HoldConfirmed, request_booking -> Booked/LostRace,
# escalate_to_human -> Escalated, and Rejected as the universal failure
# shape). The remaining tools (resolve_driver_context, get_shipment_state,
# record_eta_update, record_driver_constraint, get_appointment_status,
# release_hold, cancel_appointment) aren't named there, but the stated
# principle — "every tool returns JSON with a status field, read it first" —
# still applies to them, so they get their own status-discriminated shapes
# rather than being forced into an unrelated model.


class ShipmentSummary(BaseModel):
    shipment_id: str
    order_reference: str
    current_status: str
    status_label: str  # plain-English status, for driver-facing text
    destination_city: str
    priority_code: str


class DriverContext(BaseModel):
    status: Literal["driver_context"]
    driver_id: str
    active_shipments: list[ShipmentSummary]


class ShipmentInfo(BaseModel):
    shipment_id: str
    current_status: str
    required_dock_type: str
    temperature_control_required: bool
    load_weight_kg: int
    expected_unload_min: int
    priority_code: str


class CurrentAppointment(BaseModel):
    appointment_id: str
    appointment_status: str
    dock_code: str
    span_start_ts: str | None
    span_end_ts: str | None


class LatestEta(BaseModel):
    effective_eta_ts: str
    eta_confidence: str
    eta_source: str


class Checkin(BaseModel):
    gate_in_ts: str | None
    queue_state: str | None
    actual_dock_id: str | None


class ActiveDockEvent(BaseModel):
    event_type: str
    reason: str
    event_start_ts: str
    event_end_ts: str | None


class PendingNotification(BaseModel):
    delivery_status: str
    sent_at: str


class ActiveEscalation(BaseModel):
    escalation_id: str
    reason_code: str
    status: str
    created_at: str
    assigned_contact_id: str | None


class ShipmentState(BaseModel):
    status: Literal["shipment_state"]
    shipment: ShipmentInfo
    current_appointment: CurrentAppointment | None
    latest_eta: LatestEta | None
    checkin: Checkin | None
    active_dock_events: list[ActiveDockEvent]
    pending_notification: PendingNotification | None
    active_escalation: ActiveEscalation | None


class Recorded(BaseModel):
    status: Literal["recorded"]
    record_id: str


class AppointmentStatus(BaseModel):
    status: Literal["appointment_status"]
    appointment_id: str
    appointment_status: str
    dock_code: str | None
    span_start: str | None
    span_end: str | None


class Released(BaseModel):
    status: Literal["released"]
    hold_group_id: str


class Cancelled(BaseModel):
    status: Literal["cancelled"]
    appointment_id: str


ToolResult = Union[
    Booked,
    Rejected,
    LostRace,
    Escalated,
    Options,
    HoldConfirmed,
    DriverContext,
    ShipmentState,
    Recorded,
    AppointmentStatus,
    Released,
    Cancelled,
]


# --------------------------------------------------------------------------
# Option tokens — signed with HMAC, carried opaquely by the conversation
# layer between find_feasible_slots and hold_slot.
# --------------------------------------------------------------------------


class OptionToken(BaseModel):
    span_slot_ids: list[str]
    shipment_id: str
    state_hash: str
    issued_at: str
    signature: str


# --------------------------------------------------------------------------
# Policy configs — loaded from versioned YAML at startup (I10). A bad
# weight or missing field must fail immediately, not silently at decision
# time.
# --------------------------------------------------------------------------


class AllocationPolicy(BaseModel):
    version: str
    approved_by: str
    importance_points: dict[Literal["CRITICAL", "HIGH", "NORMAL", "LOW"], float]
    waiting_linear: float  # per hour of INVOLUNTARY waiting (I9)
    waiting_quadratic: float  # anti-starvation
    facility_at_fault: float  # queue_state = WAITING_DOCK_UNAVAILABLE
    perishable: float
    hours_past_booked_slot: float
    eta_uncertainty: dict[Literal["HIGH", "MEDIUM", "LOW"], float]
    carrier_fairness_per_contested_win: float  # suppressed when facility_at_fault
    tie_break: list[str]


class AllocationBand(BaseModel):
    max_ratio: float | None  # None = catch-all
    ttl_seconds: int


class HoldPolicy(BaseModel):
    version: str
    mode: Literal["adaptive", "fixed"]
    fixed_ttl_seconds: int
    bands: list[AllocationBand]
    floor_seconds: int
    ceiling_seconds: int
    max_extensions: int


class PendingConfirmationPolicy(BaseModel):
    version: str
    delivered_expiry_minutes: int  # booked_at + this, capped by pre_span_buffer
    pre_span_buffer_minutes: int  # never expire later than span_start - this
    queued_timeout_seconds: int  # QUEUED beyond this is treated as a failure
    retry_attempts: int
    channel_ladder: list[str]  # e.g. ["EMAIL", "SMS"]


class ReschedulePolicy(BaseModel):
    version: str
    enabled: bool
    max_reschedules_per_shipment: int
    min_notice_minutes: int


class OperatingConstants(BaseModel):
    version: str
    eta_buffer_minutes: int  # F1: span_start >= effective_eta + this
    escalation_acknowledge_minutes: int  # acknowledge_due_at = created + this
    search_band_hours: int  # relevance band width: ETA+buffer -> ETA+this
    max_options_returned: int  # find_feasible_slots returns up to this many
