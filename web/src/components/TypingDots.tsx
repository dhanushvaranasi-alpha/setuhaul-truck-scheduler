export function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 px-1 py-1" aria-label="Dispatch is typing">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/40 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/40 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/40" />
    </span>
  );
}
