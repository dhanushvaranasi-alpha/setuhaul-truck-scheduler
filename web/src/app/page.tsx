"use client";

import { useEffect, useRef, useState } from "react";
import { getThreadState, listDrivers, sendChatMessage } from "@/lib/api";
import type { Driver, ThreadState } from "@/lib/types";
import { HoldCountdown } from "@/components/HoldCountdown";

const STATUS_COLOR: Record<string, string> = {
  CONFIRMED: "text-green",
  PENDING_CONFIRMATION: "text-amber",
  CANCELLED: "text-red",
  NO_SHOW: "text-red",
  REJECTED: "text-red",
  IN_PROGRESS: "text-slate",
  COMPLETED: "text-ink/50",
};

export default function ChatPage() {
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [driverId, setDriverId] = useState<string>("");
  const [state, setState] = useState<ThreadState | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    listDrivers()
      .then((r) => {
        if (cancelled) return;
        setDrivers(r.drivers);
        if (r.drivers.length > 0) setDriverId(r.drivers[0].driver_id);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async (id: string) => {
    try {
      const s = await getThreadState(id);
      setState(s);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (!driverId) return;
    let cancelled = false;
    getThreadState(driverId)
      .then((s) => {
        if (!cancelled) {
          setState(s);
          setError(null);
        }
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [driverId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [state?.messages.length]);

  const onSend = async () => {
    if (!input.trim() || !driverId) return;
    setSending(true);
    setError(null);
    try {
      await sendChatMessage(driverId, input.trim());
      setInput("");
      await refresh(driverId);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mx-auto flex h-dvh w-full max-w-4xl flex-col">
      <header className="flex flex-col gap-2 border-b border-paper-dim px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-2xl leading-none label-track">SetuHaul</h1>
          <p className="text-xs text-ink/60">Dispatch chat</p>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="driver" className="text-[11px] text-ink/50 label-track">
            Demo only — standing in for OTP auth
          </label>
          <select
            id="driver"
            value={driverId}
            onChange={(e) => setDriverId(e.target.value)}
            className="rounded border border-paper-dim bg-white px-2 py-1.5 font-data text-sm"
          >
            {drivers.map((d) => (
              <option key={d.driver_id} value={d.driver_id}>
                {d.driver_id} — {d.driver_name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error && (
        <div className="border-b border-red/30 bg-red/10 px-4 py-2 text-sm text-red">{error}</div>
      )}

      <div className="grid flex-1 grid-cols-1 overflow-hidden sm:grid-cols-[1fr_260px]">
        <div className="flex flex-col overflow-hidden">
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {state?.messages.length === 0 && (
              <p className="text-sm text-ink/40">No messages yet. Say what&apos;s going on.</p>
            )}
            {state?.messages.map((m, i) => (
              <div
                key={i}
                className={`flex ${m.sender_type === "DRIVER" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                    m.sender_type === "DRIVER"
                      ? "bg-ink text-paper"
                      : "border border-paper-dim bg-white text-ink"
                  }`}
                >
                  <p>{m.message_text}</p>
                  <p className="mt-1 font-data text-[10px] opacity-50">
                    {new Date(m.message_ts).toLocaleTimeString()}
                  </p>
                </div>
              </div>
            ))}
          </div>
          <div className="flex items-end gap-2 border-t border-paper-dim px-4 py-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              rows={2}
              placeholder="Tell dispatch what's going on..."
              className="flex-1 resize-none rounded border border-paper-dim bg-white px-3 py-2 text-sm"
            />
            <button
              onClick={onSend}
              disabled={sending || !input.trim()}
              className="rounded bg-ink px-4 py-2 text-sm font-medium text-paper disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </div>

        <aside className="space-y-4 overflow-y-auto border-t border-paper-dim px-4 py-4 sm:border-t-0 sm:border-l">
          <section>
            <h2 className="font-display text-sm label-track text-ink/60">Shipments</h2>
            <ul className="mt-2 space-y-2">
              {state?.shipments.map((s) => (
                <li key={s.shipment_id} className="rounded border border-paper-dim bg-white p-2">
                  <p className="font-data text-xs">{s.shipment_id}</p>
                  <p className="text-xs text-ink/60">{s.order_reference}</p>
                  <p className={`text-xs font-medium ${STATUS_COLOR[s.current_status] ?? ""}`}>
                    {s.current_status}
                  </p>
                </li>
              ))}
              {state?.shipments.length === 0 && (
                <li className="text-xs text-ink/40">No active shipments.</li>
              )}
            </ul>
          </section>

          <section>
            <h2 className="font-display text-sm label-track text-ink/60">Appointment</h2>
            <ul className="mt-2 space-y-2">
              {state?.appointments.map((a) => (
                <li key={a.appointment_id} className="rounded border border-paper-dim bg-white p-2">
                  <p className="font-data text-xs">{a.appointment_id}</p>
                  <p className="text-xs">
                    Dock {a.dock_code ?? "—"} ·{" "}
                    <span className={STATUS_COLOR[a.appointment_status] ?? ""}>
                      {a.appointment_status}
                    </span>
                  </p>
                  {a.span_start && (
                    <p className="font-data text-[11px] text-ink/60">
                      {new Date(a.span_start).toLocaleTimeString()} –{" "}
                      {a.span_end && new Date(a.span_end).toLocaleTimeString()}
                    </p>
                  )}
                </li>
              ))}
              {state?.appointments.length === 0 && (
                <li className="text-xs text-ink/40">No current appointment.</li>
              )}
            </ul>
          </section>

          {state?.active_hold && (
            <section>
              <h2 className="font-display text-sm label-track text-ink/60">Active hold</h2>
              <div className="mt-2 rounded border border-amber/40 bg-amber/10 p-2">
                <p className="font-data text-xs">{state.active_hold.hold_group_id}</p>
                <p className="mt-1 text-xs text-ink/60">Expires in</p>
                <HoldCountdown expiresAt={state.active_hold.expires_at} />
              </div>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
