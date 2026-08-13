"use client";

import { useEffect, useState } from "react";
import { splitHoldMessage } from "@/lib/holdMessage";

function formatCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Replaces the static "Holding until HH:MM IST." line of an already-sent
// hold-confirmation message with a live countdown. expiresAt is anchored to
// the backend's simulated demo clock, not real time, so the countdown ticks
// against Date.now() calibrated by nowIso (that same simulated clock's
// "now" at fetch time) — see HoldCountdown for why a raw Date.now() compare
// would show every hold as permanently expired. Purely a client-side visual
// transition: at zero it swaps the whole bubble for an expiry notice,
// independent of whether the backend's own sweep has actually run yet.
// Pass key={nowIso} at the call site to force fresh calibration on a new
// simulated "now" instead of syncing state in an effect.
export function HoldConfirmationMessage({
  text,
  expiresAt,
  nowIso,
}: {
  text: string;
  expiresAt: string;
  nowIso: string;
}) {
  const target = new Date(expiresAt).getTime();
  const [offsetMs] = useState(() => new Date(nowIso).getTime() - Date.now());
  const [remaining, setRemaining] = useState(() => target - (Date.now() + offsetMs));

  useEffect(() => {
    const id = setInterval(() => setRemaining(target - (Date.now() + offsetMs)), 1000);
    return () => clearInterval(id);
  }, [target, offsetMs]);

  if (remaining <= 0) {
    return <p className="whitespace-pre-line">⚠️ Hold expired — the slot was released.</p>;
  }

  const lines = splitHoldMessage(text);
  return (
    <p className="whitespace-pre-line">
      {lines.map((l, i) => (
        <span key={i}>
          {l.isHoldLine ? (
            <span className="font-data tabular-nums text-amber">
              Holding for {formatCountdown(remaining)} ⏱
            </span>
          ) : (
            l.line
          )}
          {i < lines.length - 1 ? "\n" : ""}
        </span>
      ))}
    </p>
  );
}
