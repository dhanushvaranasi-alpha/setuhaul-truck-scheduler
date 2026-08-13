export type Driver = {
  driver_id: string;
  driver_name: string;
  home_base_city: string | null;
};

export type Shipment = {
  shipment_id: string;
  order_reference: string;
  current_status: string;
  priority_code: string;
};

export type ChatMessage = {
  sender_type: "DRIVER" | "AGENT" | "OPERATIONS" | "WAREHOUSE" | "SYSTEM";
  message_text: string;
  message_ts: string;
};

export type AppointmentSummary = {
  appointment_id: string;
  shipment_id: string;
  appointment_status: string;
  dock_code: string | null;
  span_start: string | null;
  span_end: string | null;
};

export type ActiveHold = {
  hold_group_id: string;
  shipment_id: string;
  expires_at: string;
  ttl_seconds: number;
  contention_ratio: number;
  dock_id: string;
};

export type ThreadState = {
  driver_id: string;
  now: string;
  shipments: Shipment[];
  thread_id: string | null;
  messages: ChatMessage[];
  appointments: AppointmentSummary[];
  active_hold: ActiveHold | null;
};

export type DockInfo = { dock_id: string; dock_code: string; dock_type: string };

export type DockSpan = {
  appointment_id: string;
  dock_id: string;
  shipment_id: string;
  status: string;
  span_start: string;
  span_end: string;
  driver_id: string;
  driver_name: string;
};

export type DockHold = {
  hold_group_id: string;
  dock_id: string;
  shipment_id: string;
  expires_at: string;
  span_start: string;
  span_end: string;
  driver_id: string;
  driver_name: string;
};

export type BlockedWindow = {
  dock_id: string;
  event_type: string;
  start: string;
  end: string | null;
};

export type DockTimeline = {
  facility_id: string;
  docks: DockInfo[];
  spans: DockSpan[];
  holds: DockHold[];
  blocked_windows: BlockedWindow[];
};

export type QueueEntry = {
  shipment_id: string;
  driver_id: string | null;
  driver_name: string | null;
  total: number;
  breakdown: Record<string, number>;
};

export type YardQueue = {
  facility_id: string;
  queue: QueueEntry[];
};

export type EscalationEntry = {
  escalation_id: string;
  shipment_id: string | null;
  reason_code: string;
  status: string;
  acknowledge_due_at: string;
  created_at: string;
  contact_ladder_position: number;
  assigned_to: string | null;
  driver_id: string | null;
  driver_name: string | null;
  overdue: boolean;
};

export type EscalationsResponse = {
  now: string;
  escalations: EscalationEntry[];
};

export type Metrics = {
  facility_id: string;
  conflicts: number;
  utilisation_pct: number;
  options_later_infeasible: number;
};
