import type { Metrics } from "@/lib/types";

export function MetricsPanel({ data }: { data: Metrics }) {
  const stats = [
    {
      label: "Conflicts",
      value: data.conflicts,
      tone: data.conflicts === 0 ? "text-green" : "text-red",
      note: "overlapping bookings on one dock — must read 0",
    },
    { label: "Utilisation", value: `${data.utilisation_pct}%`, tone: "text-amber", note: "slots occupied" },
    {
      label: "Infeasible later",
      value: data.options_later_infeasible,
      tone: "text-slate",
      note: "no same-day / no feasible slot",
    },
  ];

  return (
    <div className="flex h-full flex-col">
      <h2 className="mb-2 font-display text-sm label-track text-paper/60">Live metrics</h2>
      <div className="grid flex-1 grid-cols-3 gap-2">
        {stats.map((s) => (
          <div
            key={s.label}
            className="flex flex-col justify-between rounded border border-line bg-panel p-2"
          >
            <span className="text-[10px] text-paper/50 label-track">{s.label}</span>
            <span className={`font-data text-2xl font-medium ${s.tone}`}>{s.value}</span>
            <span className="text-[10px] text-paper/40">{s.note}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
