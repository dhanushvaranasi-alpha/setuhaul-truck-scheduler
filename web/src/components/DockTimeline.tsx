"use client";

import { useMemo, useState } from "react";
import type { DockTimeline as DockTimelineData } from "@/lib/types";
import { dayWindow, formatIstDate, formatIstTime, pctInWindow } from "@/lib/time";

const STATUS_BAR: Record<string, string> = {
  CONFIRMED: "bg-green",
  PENDING_CONFIRMATION: "bg-amber",
  IN_PROGRESS: "bg-slate",
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

      <div className="relative flex-1 overflow-hidden rounded border border-line bg-panel">
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
              <div className="w-16 shrink-0 border-r border-line px-2 font-data text-xs text-paper/70">
                {dock.dock_code}
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
                    return (
                      <div
                        key={s.appointment_id}
                        title={`${s.shipment_id} · ${s.status} · ${formatIstTime(s.span_start)}-${formatIstTime(s.span_end)}`}
                        className={`absolute top-1.5 h-7 rounded-sm ${STATUS_BAR[s.status] ?? "bg-slate"} opacity-90`}
                        style={{ left: `${left}%`, width: `${Math.max(right - left, 0.6)}%` }}
                      />
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
                        title={`${h.shipment_id} · HELD · expires ${formatIstTime(h.expires_at)}`}
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
      </div>
    </div>
  );
}
