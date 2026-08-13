export type QuickReply = { label: string; value: string };

const NUMBERED_EMOJI = ["1️⃣", "2️⃣", "3️⃣"];

// Three trigger patterns, checked in order — A takes priority over B, B over
// C, since a numbered list can otherwise also end with a phrase that would
// match C's tail check.
export function parseQuickReplies(text: string): QuickReply[] | null {
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  // Pattern A: numbered options (slots or shipments) — one button per
  // emoji-numbered line, sending "1" / "2" / "3" when tapped.
  const numbered = lines.filter((l) => NUMBERED_EMOJI.some((e) => l.startsWith(e)));
  if (numbered.length >= 2) {
    return numbered.map((line, i) => ({ label: line, value: String(i + 1) }));
  }

  const lower = text.toLowerCase();

  // Pattern B: hold confirmation.
  if (lower.includes("holding until") || lower.includes("held until")) {
    return [
      { label: "✅ Confirm", value: "confirm" },
      { label: "❌ Pick another", value: "pick another" },
    ];
  }

  // Pattern C: yes/no or 1-or-2 clarification, keyed off the literal tail.
  const trimmedLower = text.trim().toLowerCase().replace(/\.$/, "");
  if (trimmedLower.endsWith("reply yes or no")) {
    return [
      { label: "Yes", value: "yes" },
      { label: "No", value: "no" },
    ];
  }
  if (trimmedLower.endsWith("reply 1 or 2")) {
    return [
      { label: "1", value: "1" },
      { label: "2", value: "2" },
    ];
  }

  return null;
}
