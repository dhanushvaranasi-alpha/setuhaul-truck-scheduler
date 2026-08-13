import type { ActiveException } from "./types";
import { formatIstTime } from "./time";

export const DEFAULT_INPUT_PLACEHOLDER = "Tell dispatch what's going on...";

// One line per driver_exceptions.exception_type (schema_postgres.sql) — a
// plausible next message for a driver already mid-exception, not a
// transcript of what they already reported.
export function ghostSuggestion(exception: ActiveException | null): string {
  if (!exception) return DEFAULT_INPUT_PLACEHOLDER;
  const eta = exception.declared_eta ? formatIstTime(exception.declared_eta) : null;

  switch (exception.exception_type) {
    case "TRAFFIC":
      return eta ? `Still stuck in traffic, new ETA around ${eta}` : "Still stuck in traffic, running late";
    case "BREAKDOWN":
      return eta ? `Breakdown sorted, can reach around ${eta}` : "Breakdown sorted, running late";
    case "WEATHER":
      return eta ? `Weather's slowing me down, ETA around ${eta}` : "Weather's slowing me down";
    case "DELAY":
      return exception.reported_delay_min
        ? `Running about ${exception.reported_delay_min} min late`
        : "Running late";
    case "EARLY_ARRIVAL":
      return eta ? `Reaching earlier than planned, around ${eta}` : "Reaching earlier than planned";
    case "DOCK_UNAVAILABLE":
      return "Still waiting on a dock update";
    default:
      return "Following up on my earlier message";
  }
}
