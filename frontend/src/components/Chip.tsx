interface Props {
  children: React.ReactNode;
  tone?: "neutral" | "accent";
}

export function Chip({ children, tone = "neutral" }: Props) {
  const estilo =
    tone === "accent"
      ? "border-accent/30 bg-accent/10 text-accent"
      : "border-border bg-bg text-muted";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${estilo}`}
    >
      {children}
    </span>
  );
}
