import { STATUS_EDITAVEIS, STATUS_LABEL, type Status, type VagaResumo } from "../types";
import { ScoreBadge } from "./ScoreBadge";

interface Props {
  vaga: VagaResumo;
  onStatusChange: (id: string, status: Status) => void;
  atualizando: boolean;
}

export function VagaCard({ vaga, onStatusChange, atualizando }: Props) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-base font-medium text-text">
            {vaga.titulo}
          </h3>
          <ScoreBadge score={vaga.score_final} />
        </div>
        <p className="mt-1 text-sm text-muted">
          {vaga.empresa} · {vaga.regime} · {vaga.localizacao || "local não informado"} ·{" "}
          {vaga.nivel_real}
        </p>
        {vaga.link && (
          <a
            href={vaga.link}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-block text-sm text-accent hover:underline"
          >
            Ver vaga original ↗
          </a>
        )}
      </div>

      <select
        value={vaga.status}
        disabled={atualizando || vaga.status === "descartada"}
        onChange={(e) => onStatusChange(vaga.id, e.target.value as Status)}
        className="shrink-0 rounded-md border border-border bg-bg px-3 py-1.5 text-sm text-text disabled:opacity-50"
      >
        {vaga.status === "descartada" && (
          <option value="descartada">{STATUS_LABEL.descartada}</option>
        )}
        {STATUS_EDITAVEIS.map((s) => (
          <option key={s} value={s}>
            {STATUS_LABEL[s]}
          </option>
        ))}
      </select>
    </div>
  );
}
