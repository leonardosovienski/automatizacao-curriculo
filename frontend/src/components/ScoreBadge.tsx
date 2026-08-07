interface Props {
  score: number | null;
}

export function ScoreBadge({ score }: Props) {
  if (score === null) {
    return (
      <span className="rounded-full bg-danger/15 px-2.5 py-1 text-xs font-medium text-danger">
        Descartada
      </span>
    );
  }
  const cor =
    score >= 80 ? "text-success" : score >= 60 ? "text-warn" : "text-muted";
  return (
    <span className={`text-lg font-semibold tabular-nums ${cor}`}>
      {score.toFixed(0)}
    </span>
  );
}
