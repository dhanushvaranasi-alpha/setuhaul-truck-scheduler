import type { EscalationsResponse } from "@/lib/types";

function minutesLeft(now: string, dueAt: string): number {
  return Math.round((new Date(dueAt).getTime() - new Date(now).getTime()) / 60000);
}

function dueLabel(now: string, dueAt: string, overdue: boolean): string {
  const mins = Math.abs(minutesLeft(now, dueAt));
  if (mins === 0) return "due now";
  return overdue ? `+${mins}m overdue` : `${mins}m left`;
}

export function EscalationsPanel({ data }: { data: EscalationsResponse }) {
  return (
    <div className="flex h-full flex-col">
      <h2 className="mb-2 font-display text-sm label-track text-paper/60">Open escalations</h2>
      <div className="flex-1 space-y-1.5 overflow-y-auto">
        {data.escalations.length === 0 && (
          <p className="text-xs text-paper/40">None open — nothing waiting on dispatch.</p>
        )}
        {data.escalations.map((e) => {
          return (
            <div
              key={e.escalation_id}
              className={`rounded border px-2 py-1.5 ${
                e.overdue ? "border-red/50 bg-red/10" : "border-line bg-panel"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-data text-xs text-paper/70">
                  {e.shipment_id ?? "unassigned"}
                </span>
                <span className={`font-data text-xs ${e.overdue ? "text-red" : "text-paper/50"}`}>
                  {dueLabel(data.now, e.acknowledge_due_at, e.overdue)}
                </span>
              </div>
              <p className="mt-0.5 text-[11px] text-paper/60">{e.reason_code}</p>
              {e.driver_name && (
                <p className="text-[10px] text-paper/40">
                  {e.driver_name} · {e.driver_id}
                </p>
              )}
              <p className="text-[10px] text-paper/40">
                ladder position {e.contact_ladder_position} · {e.assigned_to ?? "unassigned"}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
