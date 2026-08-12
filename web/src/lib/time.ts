const IST_TZ = "Asia/Kolkata";

export function formatIstTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    timeZone: IST_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatIstDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    timeZone: IST_TZ,
    day: "2-digit",
    month: "short",
  });
}

/** Facility open/close for a given ISO instant's IST calendar day, as a
 * [startMs, endMs] window. Hardcoded to 06:00-22:00 (Jaipur's hours) — the
 * timeline defaults to the demo snapshot day when no data is present. */
export function dayWindow(referenceIso: string, openHour = 6, closeHour = 22): [number, number] {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: IST_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(referenceIso));
  const y = parts.find((p) => p.type === "year")!.value;
  const m = parts.find((p) => p.type === "month")!.value;
  const d = parts.find((p) => p.type === "day")!.value;
  const start = new Date(`${y}-${m}-${d}T00:00:00+05:30`).getTime() + openHour * 3600_000;
  const end = new Date(`${y}-${m}-${d}T00:00:00+05:30`).getTime() + closeHour * 3600_000;
  return [start, end];
}

export function pctInWindow(iso: string, [start, end]: [number, number]): number {
  const t = new Date(iso).getTime();
  return Math.min(100, Math.max(0, ((t - start) / (end - start)) * 100));
}
