"use client";

import { useEffect, useState } from "react";

function formatRemaining(ms: number): string {
  if (ms <= 0) return "expired";
  const totalSeconds = Math.floor(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// expiresAt comes from the backend's simulated demo clock (clock.py) —
// frozen at a fixed dataset date, not real time — so it drifts further from
// the browser's own Date.now() every day the demo is run; comparing
// expiresAt directly against Date.now() would show every hold as
// permanently expired. nowIso is that same simulated clock's "now" at
// fetch time — the gap between it and Date.now() is calibrated once and
// held constant, so the countdown still ticks in real seconds without the
// backend clock needing to move at all.
// Pass key={nowIso} at the call site — that forces a remount (fresh offset
// calibration) whenever a new simulated "now" arrives, instead of syncing
// state in an effect.
export function HoldCountdown({ expiresAt, nowIso }: { expiresAt: string; nowIso: string }) {
  const target = new Date(expiresAt).getTime();
  const [offsetMs] = useState(() => new Date(nowIso).getTime() - Date.now());
  const [remaining, setRemaining] = useState(() => target - (Date.now() + offsetMs));

  useEffect(() => {
    const id = setInterval(() => setRemaining(target - (Date.now() + offsetMs)), 1000);
    return () => clearInterval(id);
  }, [target, offsetMs]);

  const expired = remaining <= 0;
  return (
    <span
      className={`font-data text-sm tabular-nums ${expired ? "text-red" : "text-amber"}`}
      aria-live="polite"
    >
      {formatRemaining(remaining)}
    </span>
  );
}
