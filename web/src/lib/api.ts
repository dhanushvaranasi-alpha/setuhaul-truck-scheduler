import type {
  Driver,
  DockTimeline,
  EscalationsResponse,
  Metrics,
  ThreadState,
  YardQueue,
} from "./types";

// Same-origin relative paths — the Python API deploys under /api on the
// same Vercel project (see ../../vercel.json). No base URL needed.

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${path} failed: ${res.status} ${body}`);
  }
  return res.json() as Promise<T>;
}

export function listDrivers(): Promise<{ drivers: Driver[] }> {
  return getJson("/api/drivers");
}

export function getThreadState(driverId: string): Promise<ThreadState> {
  return getJson(`/api/threads?driver_id=${encodeURIComponent(driverId)}`);
}

export async function sendChatMessage(
  driverId: string,
  message: string,
): Promise<{ reply: string }> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ driver_id: driverId, message }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`chat failed: ${res.status} ${body}`);
  }
  return res.json();
}

export function getDockTimeline(facilityId?: string): Promise<DockTimeline> {
  const qs = facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : "";
  return getJson(`/api/dashboard/docks${qs}`);
}

export function getYardQueue(facilityId?: string): Promise<YardQueue> {
  const qs = facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : "";
  return getJson(`/api/dashboard/queue${qs}`);
}

export function getEscalations(): Promise<EscalationsResponse> {
  return getJson("/api/dashboard/escalations");
}

export function getMetrics(facilityId?: string): Promise<Metrics> {
  const qs = facilityId ? `?facility_id=${encodeURIComponent(facilityId)}` : "";
  return getJson(`/api/dashboard/metrics${qs}`);
}
