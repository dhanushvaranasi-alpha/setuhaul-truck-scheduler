"use client";

import { useMemo, useState } from "react";
import type { DockSpan, DockTimeline as DockTimelineData } from "@/lib/types";
import { dayWindow, formatIstDate, formatIstTime, pctInWindow } from "@/lib/time";

const STATUS_BAR: Record<string, string> = {
  CONFIRMED: "bg-green",
  PENDING_CONFIRMATION: "bg-amber",
  IN_PROGRESS: "bg-slate",
};

// Dock capability, not appointment status — deliberately distinct colors
// from STATUS_BAR above so the two dimensions don't read as the same thing.
// STANDARD gets no badge; it's the default, nothing to call out.
const DOCK_TYPE_BADGE: Record<string, string> = {
  REEFER: "text-slate border-slate/40",
  HEAVY: "text-paper/80 border-paper/30",
};

const HOUR_MARKS = [6, 9, 12, 15, 18, 21];

export function DockTimeline({ data }: { data: DockTimelineData }) {
  const days = useMemo(() => {
    const set = new Set<string>();
    for (const s of data.spans) set.add(formatIstDate(s.span_start));
    for (const h of data.holds) set.add(formatIstDate(h.span_start));
    return set.size > 0 ? Array.from(set) : [formatIstDate(new Date().toISOString())];
  }, [data]);
  const [dayLabel, setDayLabel] = useState(days[0]);
  const [selected, setSelected] = useState<DockSpan | null>(null);

  const anchorIso =
    data.spans.find((s) => formatIstDate(s.span_start) === dayLabel)?.span_start ??
    data.holds.find((h) => formatIstDate(h.span_start) === dayLabel)?.span_start ??
    new Date().toISOString();
  const window = dayWindow(anchorIso);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-display text-sm label-track text-paper/60">Dock timeline</h2>
        <div className="flex gap-1">
          {days.map((d) => (
            <button
              key={d}
              onClick={() => setDayLabel(d)}
              className={`rounded px-2 py-0.5 font-data text-[11px] ${
                d === dayLabel ? "bg-panel-raised text-paper" : "text-paper/40"
              }`}
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      <div className="relative overflow-hidden rounded border border-line bg-panel">
        {/* hour ruler */}
        <div className="relative h-5 border-b border-line">
          {HOUR_MARKS.map((h) => (
            <span
              key={h}
              className="absolute top-0.5 font-data text-[10px] text-paper/40"
              style={{ left: `${((h - 6) / 16) * 100}%` }}
            >
              {h.toString().padStart(2, "0")}:00
            </span>
          ))}
        </div>

        <div className="divide-y divide-line">
          {data.docks.map((dock) => (
            <div key={dock.dock_id} className="relative flex h-10 items-center">
              <div
                className="flex w-20 shrink-0 flex-col justify-center gap-0.5 border-r border-line px-2"
                title={dock.dock_type}
              >
                <span className="font-data text-xs text-paper/70">{dock.dock_code}</span>
                {DOCK_TYPE_BADGE[dock.dock_type] && (
                  <span
                    className={`w-fit rounded-sm border px-1 font-data text-[8px] leading-tight label-track ${DOCK_TYPE_BADGE[dock.dock_type]}`}
                  >
                    {dock.dock_type}
                  </span>
                )}
              </div>
              <div className="relative h-full flex-1">
                {data.blocked_windows
                  .filter((b) => b.dock_id === dock.dock_id)
                  .map((b, i) => {
                    const left = pctInWindow(b.start, window);
                    const right = b.end ? pctInWindow(b.end, window) : 100;
                    return (
                      <div
                        key={i}
                        title={`${b.event_type}`}
                        className="absolute top-0 h-full bg-[repeating-linear-gradient(45deg,rgba(193,80,63,0.25)_0,rgba(193,80,63,0.25)_4px,transparent_4px,transparent_8px)]"
                        style={{ left: `${left}%`, width: `${right - left}%` }}
                      />
                    );
                  })}
                {data.spans
                  .filter((s) => s.dock_id === dock.dock_id && formatIstDate(s.span_start) === dayLabel)
                  .map((s) => {
                    const left = pctInWindow(s.span_start, window);
                    const right = pctInWindow(s.span_end, window);
                    const confirmed = s.status === "CONFIRMED";
                    return (
                      <div
                        key={s.appointment_id}
                        title={`${s.shipment_id} · ${s.driver_name} · ${s.status} · ${formatIstTime(s.span_start)}-${formatIstTime(s.span_end)}`}
                        onClick={confirmed ? () => setSelected(s) : undefined}
                        className={`absolute top-1.5 flex h-7 items-center overflow-hidden rounded-sm ${STATUS_BAR[s.status] ?? "bg-slate"} opacity-90 ${
                          confirmed ? "cursor-pointer ring-1 ring-inset ring-ink/0 hover:ring-ink/40" : ""
                        }`}
                        style={{ left: `${left}%`, width: `${Math.max(right - left, 0.6)}%` }}
                      >
                        {confirmed && (
                          <span className="truncate px-1 font-data text-[9px] leading-none text-ink/80">
                            {s.shipment_id}
                          </span>
                        )}
                      </div>
                    );
                  })}
                {data.holds
                  .filter((h) => h.dock_id === dock.dock_id && formatIstDate(h.span_start) === dayLabel)
                  .map((h) => {
                    const left = pctInWindow(h.span_start, window);
                    const right = pctInWindow(h.span_end, window);
                    return (
                      <div
                        key={h.hold_group_id}
                        title={`${h.shipment_id} · ${h.driver_name} · HELD · expires ${formatIstTime(h.expires_at)}`}
                        className="absolute top-1.5 h-7 rounded-sm border-2 border-dashed border-amber bg-amber/20"
                        style={{ left: `${left}%`, width: `${Math.max(right - left, 0.6)}%` }}
                      />
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-3 font-data text-[10px] text-paper/50">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm bg-green" /> confirmed
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm bg-amber" /> pending
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm border border-dashed border-amber bg-amber/20" /> held
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm bg-red/40" /> breakdown / maintenance
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm border border-slate/40" /> reefer dock
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-sm border border-paper/30" /> heavy dock
        </span>
      </div>

      {selected && (
        <div className="mt-2 flex items-start justify-between rounded border border-green/40 bg-green/10 px-3 py-2">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 font-data text-[11px] text-paper/80 sm:grid-cols-4">
            <div>
              <dt className="text-paper/40">shipment</dt>
              <dd>{selected.shipment_id}</dd>
            </div>
            <div>
              <dt className="text-paper/40">driver</dt>
              <dd>
                {selected.driver_name} ({selected.driver_id})
              </dd>
            </div>
            <div>
              <dt className="text-paper/40">appointment</dt>
              <dd>{selected.appointment_id}</dd>
            </div>
            <div>
              <dt className="text-paper/40">dock</dt>
              <dd>{data.docks.find((d) => d.dock_id === selected.dock_id)?.dock_code ?? selected.dock_id}</dd>
            </div>
            <div>
              <dt className="text-paper/40">span</dt>
              <dd>
                {formatIstTime(selected.span_start)}–{formatIstTime(selected.span_end)}
              </dd>
            </div>
          </dl>
          <button
            onClick={() => setSelected(null)}
            aria-label="Close"
            className="ml-2 shrink-0 text-paper/40 hover:text-paper"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
