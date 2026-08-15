"use client";

import { useState } from "react";
import type { PendingConfirmationsResponse } from "@/lib/types";
import { formatIstTime } from "@/lib/time";
import { triggerAdminTick } from "@/lib/api";

function minutesPending(now: string, bookedAt: string): number {
  return Math.round((new Date(now).getTime() - new Date(bookedAt).getTime()) / 60000);
}

function pendingLabel(now: string, bookedAt: string): string {
  const mins = minutesPending(now, bookedAt);
  return mins <= 0 ? "just now" : `pending ${mins} min`;
}

export function PendingConfirmationPanel({
  data,
  onTicked,
}: {
  data: PendingConfirmationsResponse;
  onTicked: () => void;
}) {
  const [ticking, setTicking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // admin/tick runs the whole delivery sweep, not per-appointment — any
  // row's button drives the same global action. Ties every button to one
  // in-flight state so a second click can't fire a second tick mid-request.
  const simulateConfirm = async () => {
    setTicking(true);
    setError(null);
    try {
      await triggerAdminTick();
      onTicked();
    } catch (e) {
      setError(String(e));
    } finally {
      setTicking(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <h2 className="mb-2 font-display text-sm label-track text-paper/60">Pending confirmation</h2>
      {error && <p className="mb-1.5 text-[11px] text-red">{error}</p>}
      <div className="flex-1 space-y-1.5 overflow-y-auto">
        {data.pending.length === 0 && (
          <p className="text-xs text-paper/40">Nothing waiting on the warehouse.</p>
        )}
        {data.pending.map((p) => (
          <div
            key={p.appointment_id}
            className="flex flex-col gap-1 rounded border border-amber/40 bg-amber/10 px-2 py-1.5 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className="font-data text-xs text-paper/70">
                {p.shipment_id} · {p.driver_name}
              </span>
              <span className="font-data text-[11px] text-paper/50">
                {p.dock_code ?? "—"}{" "}
                {p.span_start && formatIstTime(p.span_start)}
                {p.span_end && `–${formatIstTime(p.span_end)}`} IST
              </span>
              <span className="font-data text-[10px] text-amber">
                {pendingLabel(data.now, p.booked_at)}
              </span>
            </div>
            <button
              onClick={simulateConfirm}
              disabled={ticking}
              className="w-fit shrink-0 rounded border border-paper/30 px-2 py-1 font-data text-[10px] text-paper/70 transition-colors hover:bg-paper/10 disabled:opacity-40"
            >
              Simulate Confirm
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
