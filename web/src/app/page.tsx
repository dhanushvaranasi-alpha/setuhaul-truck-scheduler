"use client";

import { useEffect, useRef, useState } from "react";
import { getThreadState, listDrivers, sendChatMessage } from "@/lib/api";
import type { Driver, ThreadState } from "@/lib/types";
import { formatIstTime } from "@/lib/time";
import { HoldCountdown } from "@/components/HoldCountdown";
import { HoldConfirmationMessage } from "@/components/HoldConfirmationMessage";
import { TypingDots } from "@/components/TypingDots";
import { parseQuickReplies } from "@/lib/quickReplies";
import { isHoldConfirmationMessage } from "@/lib/holdMessage";
import { appointmentStatusLabel } from "@/lib/statusLabels";
import { ghostSuggestion } from "@/lib/ghostSuggestion";

const STATUS_COLOR: Record<string, string> = {
  CONFIRMED: "text-green",
  PENDING_CONFIRMATION: "text-amber",
  CANCELLED: "text-red",
  NO_SHOW: "text-red",
  REJECTED: "text-red",
  IN_PROGRESS: "text-slate",
  COMPLETED: "text-ink/50",
};

type PendingSend = { text: string; failed: boolean };

export default function ChatPage() {
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [driverId, setDriverId] = useState<string>("");
  const [state, setState] = useState<ThreadState | null>(null);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<PendingSend | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const inFlight = pending !== null && !pending.failed;

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
          setInput("");
          setPending(null);
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
  }, [state?.messages.length, pending]);

  const sendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !driverId || inFlight) return;
    setInput("");
    setPending({ text: trimmed, failed: false });
    try {
      await sendChatMessage(driverId, trimmed);
      await refresh(driverId);
      setPending(null);
    } catch {
      setPending({ text: trimmed, failed: true });
    }
  };

  const onSend = () => sendMessage(input);

  const lastMessage = state?.messages[state.messages.length - 1];
  const quickReplies =
    lastMessage?.sender_type === "AGENT" ? parseQuickReplies(lastMessage.message_text) : null;
  // Buttons are a shortcut, not a replacement — hide them while a request is
  // in flight (or awaiting retry), or as soon as the driver starts typing.
  const showQuickReplies = quickReplies !== null && pending === null && input.trim() === "";

  const suggestion = ghostSuggestion(state?.active_exception ?? null);

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
            {state?.messages.length === 0 && !pending && (
              <p className="text-sm text-ink/40">No messages yet. Say what&apos;s going on.</p>
            )}
            {state?.messages.map((m, i) => {
              const isLast = i === state.messages.length - 1;
              const showHoldCountdown =
                isLast &&
                m.sender_type === "AGENT" &&
                state.active_hold != null &&
                isHoldConfirmationMessage(m.message_text);
              return (
                <div key={i}>
                  <div
                    className={`flex ${m.sender_type === "DRIVER" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                        m.sender_type === "DRIVER"
                          ? "bg-ink text-paper"
                          : "border border-paper-dim bg-white text-ink"
                      }`}
                    >
                      {showHoldCountdown ? (
                        <HoldConfirmationMessage
                          key={state.now}
                          text={m.message_text}
                          expiresAt={state.active_hold!.expires_at}
                          nowIso={state.now}
                        />
                      ) : (
                        <p className="whitespace-pre-line">{m.message_text}</p>
                      )}
                      <p className="mt-1 font-data text-[10px] opacity-50">
                        {formatIstTime(m.message_ts)} IST
                      </p>
                    </div>
                  </div>
                  {isLast && showQuickReplies && (
                    <div className="mt-3 flex flex-col gap-2">
                      {quickReplies!.map((qr) => (
                        <button
                          key={qr.value}
                          onClick={() => sendMessage(qr.value)}
                          className="w-full cursor-pointer rounded-full border border-[#444] bg-[#f6f2e8] px-4 py-2 text-left text-sm text-ink transition-colors hover:border-ink hover:bg-[#fbfaf6]"
                        >
                          {qr.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {pending && (
              <div>
                <div className="flex justify-end">
                  <div className="max-w-[80%] rounded-lg bg-ink px-3 py-2 text-sm text-paper">
                    <p className="whitespace-pre-line">{pending.text}</p>
                  </div>
                </div>
                {pending.failed ? (
                  <p className="mt-1 text-right">
                    <button
                      onClick={() => sendMessage(pending.text)}
                      className="cursor-pointer text-xs text-red underline decoration-dotted hover:decoration-solid"
                    >
                      Failed to send — tap to retry
                    </button>
                  </p>
                ) : (
                  <div className="mt-2 flex justify-start">
                    <div className="rounded-lg border border-paper-dim bg-white px-2 py-1.5">
                      <TypingDots />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="flex items-end gap-2 border-t border-paper-dim px-4 py-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Tab" && input.trim() === "") {
                  e.preventDefault();
                  setInput(suggestion);
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              rows={2}
              placeholder={suggestion}
              className="flex-1 resize-none rounded border border-paper-dim bg-white px-3 py-2 text-sm placeholder:text-ink/35"
            />
            <button
              onClick={onSend}
              disabled={inFlight || !input.trim()}
              className="rounded bg-ink px-4 py-2 text-sm font-medium text-paper disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </div>

        <aside className="flex flex-col overflow-y-auto border-t border-paper-dim sm:border-t-0 sm:border-l">
          <section>
            <h2 className="px-3 pt-3 font-display text-sm label-track text-ink/60">Shipments</h2>
            <div className="mt-2 border-t border-paper-dim" />
            <ul className="divide-y divide-paper-dim">
              {(state?.shipments.length ?? 0) === 0 && (
                <li className="p-3 text-xs text-ink/40">No active shipments.</li>
              )}
              {state?.shipments.map((s) => (
                <li key={s.shipment_id} className="p-3">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-data text-xs text-ink">{s.shipment_id}</span>
                    <span className="shrink-0 font-data text-[10px] text-ink/50">
                      {s.current_status}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-ink/60">{s.order_reference}</p>
                  <p className="text-xs text-ink/60">{s.destination_city}</p>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="px-3 pt-4 font-display text-sm label-track text-ink/60">Appointment</h2>
            <div className="mt-2 border-t border-paper-dim" />
            <ul className="divide-y divide-paper-dim">
              {(state?.appointments.length ?? 0) === 0 && (
                <li className="p-3 text-xs text-ink/40">No active appointment.</li>
              )}
              {state?.appointments.map((a) => (
                <li key={a.appointment_id} className="p-3">
                  <p className="text-xs text-ink">
                    Dock {a.dock_code ?? "—"}
                    {a.dock_type ? ` · ${a.dock_type}` : ""}
                  </p>
                  {a.span_start && (
                    <p className="font-data text-[11px] text-ink/60">
                      {formatIstTime(a.span_start)}
                      {a.span_end && `–${formatIstTime(a.span_end)}`} IST
                    </p>
                  )}
                  <p className={`text-xs ${STATUS_COLOR[a.appointment_status] ?? ""}`}>
                    Status: {appointmentStatusLabel(a.appointment_status)}
                  </p>
                  <p className="mt-1 text-xs text-ink/60">
                    Shipment: {a.shipment_id} · {a.destination_city}
                  </p>
                </li>
              ))}
            </ul>
            <div className="border-t border-paper-dim" />
            <div className="p-3">
              {state?.active_hold ? (
                <div className="rounded border border-amber/40 bg-amber/10 p-2">
                  <p className="text-xs text-ink">
                    Holding
                    {state.active_hold.dock_code && ` — ${state.active_hold.dock_code}`}
                    {state.active_hold.span_start && `, ${formatIstTime(state.active_hold.span_start)}`}
                    {state.active_hold.span_end && `–${formatIstTime(state.active_hold.span_end)}`} IST
                  </p>
                  <HoldCountdown key={state.now} expiresAt={state.active_hold.expires_at} nowIso={state.now} />
                </div>
              ) : (
                <p className="text-xs text-ink/30">No active hold.</p>
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
