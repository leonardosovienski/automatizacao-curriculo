import { useEffect, useMemo, useState } from "react";
import { atualizarStatus, listarVagas } from "./api";
import { VagaCard } from "./components/VagaCard";
import { STATUS_LABEL, type Status, type VagaResumo } from "./types";

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
  const [filtro, setFiltro] = useState<Status | "todas">("novo");
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [atualizandoId, setAtualizandoId] = useState<string | null>(null);

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
            "Não foi possível carregar as vagas. A API (`uvicorn api.app:app`) está rodando?",
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

  const contagem = useMemo(() => vagas.length, [vagas]);

  return (
    <div className="mx-auto min-h-screen max-w-3xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-text">Triagem de Vagas</h1>
        <p className="mt-1 text-sm text-muted">
          {carregando ? "Carregando…" : `${contagem} vaga(s)`}
        </p>
      </header>

      <div className="mb-6 flex flex-wrap gap-2">
        {FILTROS.map((f) => (
          <button
            key={f}
            onClick={() => setFiltro(f)}
            className={`rounded-full border px-3 py-1 text-sm transition ${
              filtro === f
                ? "border-accent bg-accent/15 text-accent"
                : "border-border text-muted hover:text-text"
            }`}
          >
            {f === "todas" ? "Todas" : STATUS_LABEL[f]}
          </button>
        ))}
      </div>

      {erro && (
        <div className="mb-4 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {erro}
        </div>
      )}

      {!carregando && vagas.length === 0 && !erro && (
        <p className="text-sm text-muted">Nenhuma vaga neste filtro.</p>
      )}

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
    </div>
  );
}
