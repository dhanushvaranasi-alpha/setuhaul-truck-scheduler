import type { YardQueue } from "@/lib/types";

export function YardQueuePanel({ data }: { data: YardQueue }) {
  return (
    <div className="flex h-full flex-col">
      <h2 className="mb-2 font-display text-sm label-track text-paper/60">Yard queue</h2>
      <div className="flex-1 space-y-1.5 overflow-y-auto">
        {data.queue.length === 0 && <p className="text-xs text-paper/40">No competing trucks.</p>}
        {data.queue.map((entry, rank) => (
          <div
            key={entry.shipment_id}
            className="rounded border border-line bg-panel px-2 py-1.5"
            title={Object.entries(entry.breakdown)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · ")}
          >
            <div className="flex items-center justify-between">
              <span className="font-data text-xs text-paper/70">
                <span className="text-paper/40">{rank + 1}.</span> {entry.shipment_id}
              </span>
              <span className="font-data text-sm font-medium text-amber">
                {entry.total.toFixed(2)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
