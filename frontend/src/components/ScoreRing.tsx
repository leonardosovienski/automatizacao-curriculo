interface Props {
  score: number | null;
  size?: number;
}

export function ScoreRing({ score, size = 52 }: Props) {
  if (score === null) {
    return (
      <div
        className="flex shrink-0 items-center justify-center rounded-full border-2 border-danger/40 bg-danger/10 text-[10px] font-semibold uppercase tracking-wide text-danger"
        style={{ width: size, height: size }}
      >
        Fora
      </div>
    );
  }

  const cor =
    score >= 80
      ? "var(--color-success)"
      : score >= 60
        ? "var(--color-warn)"
        : "var(--color-muted)";
  const pct = Math.max(0, Math.min(100, score));
  const raio = (size - 6) / 2;
  const circ = 2 * Math.PI * raio;
  const offset = circ - (pct / 100) * circ;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={raio}
          fill="none"
          stroke="var(--color-border)"
          strokeWidth={4}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={raio}
          fill="none"
          stroke={cor}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.4s ease" }}
        />
      </svg>
      <span
        className="absolute inset-0 flex items-center justify-center text-sm font-semibold tabular-nums"
        style={{ color: cor }}
      >
        {pct.toFixed(0)}
      </span>
    </div>
  );
}
