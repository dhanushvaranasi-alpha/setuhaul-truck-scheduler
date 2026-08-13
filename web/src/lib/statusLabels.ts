// Driver-facing wording — mirrors src/core/driver_context.py's STATUS_LABELS
// so a shipment's status reads the same in chat replies and on screen.
// Never render a raw status enum to the driver.

const SHIPMENT_STATUS_LABELS: Record<string, string> = {
  PLANNED: "Not dispatched yet",
  ASSIGNED: "Scheduled",
  IN_TRANSIT: "On the way",
  AT_GATE: "Checked in at the gate",
  WAITING: "At the gate",
  IN_DOCK: "Unloading now",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

const APPOINTMENT_STATUS_LABELS: Record<string, string> = {
  PENDING_CONFIRMATION: "Pending confirmation",
  CONFIRMED: "Confirmed",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
  NO_SHOW: "No show",
  REJECTED: "Rejected",
};

function titleCaseFallback(status: string): string {
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function shipmentStatusLabel(status: string): string {
  return SHIPMENT_STATUS_LABELS[status] ?? titleCaseFallback(status);
}

export function appointmentStatusLabel(status: string): string {
  return APPOINTMENT_STATUS_LABELS[status] ?? titleCaseFallback(status);
}
