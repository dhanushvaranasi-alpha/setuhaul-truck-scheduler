const HOLD_LINE_RE = /^Holding until \d{1,2}:\d{2} IST\.?$/i;

export function isHoldConfirmationMessage(text: string): boolean {
  return text.split("\n").some((line) => HOLD_LINE_RE.test(line.trim()));
}

/** One entry per line of the raw message; isHoldLine marks the static
 * "Holding until HH:MM IST." line the caller replaces with a live
 * countdown — everything else in the template renders unchanged. */
export function splitHoldMessage(text: string): { line: string; isHoldLine: boolean }[] {
  return text.split("\n").map((line) => ({
    line,
    isHoldLine: HOLD_LINE_RE.test(line.trim()),
  }));
}
