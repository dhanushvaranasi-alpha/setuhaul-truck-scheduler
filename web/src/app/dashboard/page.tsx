"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getDockTimeline, getEscalations, getMetrics, getPendingConfirmations, getYardQueue } from "@/lib/api";
import type {
  DockTimeline as DockTimelineData,
  EscalationsResponse,
  Metrics,
  PendingConfirmationsResponse,
  YardQueue,
} from "@/lib/types";
import { DockTimeline } from "@/components/DockTimeline";
import { PendingConfirmationPanel } from "@/components/PendingConfirmationPanel";
import { YardQueuePanel } from "@/components/YardQueuePanel";
import { EscalationsPanel } from "@/components/EscalationsPanel";
import { MetricsPanel } from "@/components/MetricsPanel";

const POLL_MS = 15000;

export default function DashboardPage() {
  const [docks, setDocks] = useState<DockTimelineData | null>(null);
  const [pending, setPending] = useState<PendingConfirmationsResponse | null>(null);
  const [queue, setQueue] = useState<YardQueue | null>(null);
  const [escalations, setEscalations] = useState<EscalationsResponse | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(() => {
    Promise.all([getDockTimeline(), getPendingConfirmations(), getYardQueue(), getEscalations(), getMetrics()])
      .then(([d, p, q, e, m]) => {
        if (!mounted.current) return;
        setDocks(d);
        setPending(p);
        setQueue(q);
        setEscalations(e);
        setMetrics(m);
        setLastRefreshed(new Date());
        setError(null);
      })
      .catch((err) => mounted.current && setError(String(err)));
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [refresh]);

  return (
    <div className="min-h-dvh bg-ink text-paper">
      <header className="flex items-center justify-between border-b border-line px-6 py-4">
        <div>
          <h1 className="font-display text-2xl label-track">SetuHaul — dispatch board</h1>
          <p className="text-xs text-paper/40">Read-only. No editing, no manual booking.</p>
        </div>
        {lastRefreshed && (
          <span className="font-data text-[11px] text-paper/40">
            updated {lastRefreshed.toLocaleTimeString()}
          </span>
        )}
      </header>

      {error && <div className="border-b border-red/30 bg-red/10 px-6 py-2 text-sm text-red">{error}</div>}

      {!docks || !pending || !queue || !escalations || !metrics ? (
        <p className="p-6 text-sm text-paper/50">Loading...</p>
      ) : (
        <div className="grid gap-4 p-4 lg:grid-cols-2 lg:grid-rows-[auto_auto_minmax(220px,1fr)]">
          <div className="rounded-lg border border-line bg-panel-raised p-4 lg:col-span-2">
            <DockTimeline data={docks} />
          </div>
          <div className="rounded-lg border border-line bg-panel-raised p-4 lg:col-span-2">
            <PendingConfirmationPanel data={pending} onTicked={refresh} />
          </div>
          <div className="rounded-lg border border-line bg-panel-raised p-4">
            <YardQueuePanel data={queue} />
          </div>
          <div className="rounded-lg border border-line bg-panel-raised p-4">
            <EscalationsPanel data={escalations} />
          </div>
          <div className="rounded-lg border border-line bg-panel-raised p-4 lg:col-span-2">
            <MetricsPanel data={metrics} />
          </div>
        </div>
      )}
    </div>
  );
}
