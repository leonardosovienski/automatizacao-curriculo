import type { Stats } from "../types";

interface Props {
  stats: Stats | null;
  erro?: boolean;
}

const ITENS: { key: keyof Stats["por_status"] | "total"; label: string }[] = [
  { key: "total", label: "Total" },
  { key: "novo", label: "Novas" },
  { key: "aplicado", label: "Aplicadas" },
  { key: "entrevista", label: "Entrevistas" },
  { key: "fechada", label: "Fechadas" },
];

export function StatsBar({ stats, erro }: Props) {
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {ITENS.map(({ key, label }) => {
          const valor =
            key === "total" ? stats?.total : stats?.por_status[key];
          return (
            <div
              key={key}
              className="rounded-lg border border-border bg-surface px-3 py-2.5"
            >
              <div className="text-xl font-semibold tabular-nums text-text">
                {stats ? valor : "–"}
              </div>
              <div className="text-xs text-muted">{label}</div>
            </div>
          );
        })}
      </div>
      {erro && !stats && (
        <p className="mt-1.5 text-xs text-danger">
          Não foi possível carregar as métricas.
        </p>
      )}
    </div>
  );
}
