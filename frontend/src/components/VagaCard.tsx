import { AlertTriangle, ChevronDown, ExternalLink } from "lucide-react";
import { useState } from "react";
import {
  DIMENSAO_LABEL,
  NIVEL_LABEL,
  REGIME_LABEL,
  STATUS_EDITAVEIS,
  STATUS_LABEL,
  type Status,
  type VagaResumo,
} from "../types";
import { Chip } from "./Chip";
import { ScoreRing } from "./ScoreRing";
import { Spinner } from "./Spinner";

interface Props {
  vaga: VagaResumo;
  onStatusChange: (id: string, status: Status) => void;
  atualizando: boolean;
}

export function VagaCard({ vaga, onStatusChange, atualizando }: Props) {
  const [aberto, setAberto] = useState(false);
  const temDetalhes =
    vaga.stack_exigida.length > 0 ||
    vaga.stack_desejavel.length > 0 ||
    vaga.alertas.length > 0 ||
    Boolean(vaga.notas) ||
    Boolean(vaga.motivo_descarte);

  return (
    <div className="rounded-xl border border-border bg-surface transition hover:border-accent/30">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={() => temDetalhes && setAberto((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <ScoreRing score={vaga.score_final} />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-medium text-text">
              {vaga.titulo}
            </h3>
            <p className="mt-0.5 truncate text-sm text-muted">
              {vaga.empresa} · {REGIME_LABEL[vaga.regime] ?? vaga.regime} ·{" "}
              {vaga.localizacao || "local não informado"}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <Chip>{NIVEL_LABEL[vaga.nivel_real] ?? vaga.nivel_real}</Chip>
              {vaga.idioma_trabalho && (
                <Chip>{vaga.idioma_trabalho.toUpperCase()}</Chip>
              )}
              {vaga.alertas.length > 0 && (
                <Chip tone="accent">
                  {vaga.alertas.length} alerta
                  {vaga.alertas.length > 1 ? "s" : ""}
                </Chip>
              )}
            </div>
          </div>
          {temDetalhes && (
            <ChevronDown
              size={16}
              className={`shrink-0 text-muted transition-transform ${aberto ? "rotate-180" : ""}`}
              aria-hidden
            />
          )}
        </button>

        <div className="flex shrink-0 items-center gap-2 sm:flex-col sm:items-end">
          <div className="relative">
            <select
              value={vaga.status}
              disabled={atualizando || vaga.status === "descartada"}
              onChange={(e) =>
                onStatusChange(vaga.id, e.target.value as Status)
              }
              aria-label={`Status da vaga ${vaga.titulo}`}
              className="rounded-md border border-border bg-bg px-3 py-1.5 text-sm text-text disabled:opacity-50"
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
            {atualizando && (
              <span className="pointer-events-none absolute -right-5 top-1/2 -translate-y-1/2">
                <Spinner size={14} />
              </span>
            )}
          </div>
          {vaga.link && (
            <a
              href={vaga.link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-sm text-accent hover:underline"
            >
              Vaga original <ExternalLink size={13} />
            </a>
          )}
        </div>
      </div>

      {aberto && temDetalhes && (
        <div className="space-y-3 border-t border-border px-4 pb-4 pt-3">
          {vaga.motivo_descarte && (
            <p className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
              {vaga.motivo_descarte}
            </p>
          )}

          {vaga.notas && (
            <div className="space-y-1.5">
              {Object.entries(vaga.notas).map(([chave, dim]) => (
                <div key={chave} className="flex gap-3 text-sm">
                  <span className="w-8 shrink-0 text-right font-medium tabular-nums text-text">
                    {dim.nota}
                  </span>
                  <div>
                    <span className="font-medium text-text">
                      {DIMENSAO_LABEL[chave] ?? chave}
                    </span>
                    <span className="text-muted"> — {dim.justificativa}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {vaga.stack_exigida.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                Stack exigida
              </p>
              <div className="flex flex-wrap gap-1.5">
                {vaga.stack_exigida.map((s) => (
                  <Chip key={s} tone="accent">
                    {s}
                  </Chip>
                ))}
              </div>
            </div>
          )}

          {vaga.stack_desejavel.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                Stack desejável
              </p>
              <div className="flex flex-wrap gap-1.5">
                {vaga.stack_desejavel.map((s) => (
                  <Chip key={s}>{s}</Chip>
                ))}
              </div>
            </div>
          )}

          {vaga.alertas.length > 0 && (
            <ul className="space-y-1.5 text-sm text-warn">
              {vaga.alertas.map((a, i) => (
                <li key={i} className="flex items-start gap-1.5">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{a}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
