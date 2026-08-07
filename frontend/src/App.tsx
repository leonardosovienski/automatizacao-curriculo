import { useEffect, useState } from "react";
import { atualizarStatus, listarVagas, obterStats } from "./api";
import { CardSkeleton } from "./components/CardSkeleton";
import { StatsBar } from "./components/StatsBar";
import { Toast } from "./components/Toast";
import { VagaCard } from "./components/VagaCard";
import { STATUS_LABEL, type Stats, type Status, type VagaResumo } from "./types";

const FILTROS: (Status | "todas")[] = [
  "todas",
  "novo",
  "aplicado",
  "entrevista",
  "recusado",
  "fechada",
  "descartada",
];

export default function App() {
  const [vagas, setVagas] = useState<VagaResumo[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filtro, setFiltro] = useState<Status | "todas">("novo");
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [atualizandoId, setAtualizandoId] = useState<string | null>(null);

  useEffect(() => {
    obterStats().catch(() => undefined).then((s) => s && setStats(s));
  }, [vagas]);

  useEffect(() => {
    let cancelado = false;
    setCarregando(true);
    setErro(null);
    listarVagas(filtro === "todas" ? undefined : filtro)
      .then((data) => {
        if (!cancelado) setVagas(data);
      })
      .catch(() => {
        if (!cancelado)
          setErro(
            "Não foi possível carregar as vagas. A API (uvicorn api.app:app) está rodando?",
          );
      })
      .finally(() => {
        if (!cancelado) setCarregando(false);
      });
    return () => {
      cancelado = true;
    };
  }, [filtro]);

  async function handleStatusChange(id: string, status: Status) {
    setAtualizandoId(id);
    try {
      const atualizada = await atualizarStatus(id, status);
      setVagas((prev) =>
        filtro === "todas" || filtro === status
          ? prev.map((v) => (v.id === id ? atualizada : v))
          : prev.filter((v) => v.id !== id),
      );
    } catch {
      setErro("Falha ao atualizar o status. Tente novamente.");
    } finally {
      setAtualizandoId(null);
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface/60">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path
                d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <div>
            <h1 className="text-lg font-semibold text-text">Triagem de Vagas</h1>
            <p className="text-xs text-muted">
              Scoring automático com Google Gemini
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        <div className="mb-6">
          <StatsBar stats={stats} />
        </div>

        <div className="mb-5 flex flex-wrap gap-2">
          {FILTROS.map((f) => (
            <button
              key={f}
              onClick={() => setFiltro(f)}
              className={`rounded-full border px-3 py-1 text-sm transition ${
                filtro === f
                  ? "border-accent bg-accent/15 text-accent"
                  : "border-border text-muted hover:border-accent/30 hover:text-text"
              }`}
            >
              {f === "todas" ? "Todas" : STATUS_LABEL[f]}
            </button>
          ))}
        </div>

        {carregando && (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )}

        {!carregando && vagas.length === 0 && !erro && (
          <div className="rounded-xl border border-dashed border-border py-14 text-center">
            <p className="text-sm text-muted">
              Nenhuma vaga com status "{STATUS_LABEL[filtro as Status] ?? "todas"}".
            </p>
            <p className="mt-1 text-xs text-muted">
              Rode <code className="rounded bg-bg px-1.5 py-0.5">triar analisar</code> ou{" "}
              <code className="rounded bg-bg px-1.5 py-0.5">triar buscar</code> para
              popular o histórico.
            </p>
          </div>
        )}

        {!carregando && vagas.length > 0 && (
          <div className="flex flex-col gap-3">
            {vagas.map((vaga) => (
              <VagaCard
                key={vaga.id}
                vaga={vaga}
                onStatusChange={handleStatusChange}
                atualizando={atualizandoId === vaga.id}
              />
            ))}
          </div>
        )}
      </main>

      {erro && <Toast message={erro} onDismiss={() => setErro(null)} />}
    </div>
  );
}
