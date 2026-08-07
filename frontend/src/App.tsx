import { CheckCircle2, HelpCircle, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { atualizarStatus, listarVagas, obterStats } from "./api";
import { CardSkeleton } from "./components/CardSkeleton";
import { Modal } from "./components/Modal";
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

type Ordenacao = "score_desc" | "score_asc" | "recente";

const ORDENACOES: { value: Ordenacao; label: string }[] = [
  { value: "score_desc", label: "Maior score" },
  { value: "score_asc", label: "Menor score" },
  { value: "recente", label: "Mais recente" },
];

function ordenar(vagas: VagaResumo[], ordenacao: Ordenacao): VagaResumo[] {
  const copia = [...vagas];
  switch (ordenacao) {
    case "score_asc":
      return copia.sort(
        (a, b) => (a.score_final ?? -1) - (b.score_final ?? -1),
      );
    case "recente":
      return copia.sort((a, b) => b.analisado_em.localeCompare(a.analisado_em));
    default:
      return copia.sort(
        (a, b) => (b.score_final ?? -1) - (a.score_final ?? -1),
      );
  }
}

export default function App() {
  const [vagas, setVagas] = useState<VagaResumo[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filtro, setFiltro] = useState<Status | "todas">("novo");
  const [busca, setBusca] = useState("");
  const [ordenacao, setOrdenacao] = useState<Ordenacao>("score_desc");
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [statsErro, setStatsErro] = useState(false);
  const [atualizandoId, setAtualizandoId] = useState<string | null>(null);
  const [ajudaAberta, setAjudaAberta] = useState(false);

  useEffect(() => {
    obterStats()
      .then((s) => {
        setStats(s);
        setStatsErro(false);
      })
      .catch(() => setStatsErro(true));
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

  const vagasFiltradas = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    const filtradas = termo
      ? vagas.filter(
          (v) =>
            v.empresa.toLowerCase().includes(termo) ||
            v.titulo.toLowerCase().includes(termo),
        )
      : vagas;
    return ordenar(filtradas, ordenacao);
  }, [vagas, busca, ordenacao]);

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface/60">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <CheckCircle2 size={18} />
          </div>
          <div className="flex-1">
            <h1 className="text-lg font-semibold text-text">Triagem de Vagas</h1>
            <p className="text-xs text-muted">
              Scoring automático com Google Gemini
            </p>
          </div>
          <button
            onClick={() => setAjudaAberta(true)}
            aria-label="Como popular o histórico"
            className="p-1.5 text-muted transition-colors hover:text-text"
          >
            <HelpCircle size={20} />
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        <div className="mb-6">
          <StatsBar stats={stats} erro={statsErro} />
        </div>

        <div className="mb-4 flex flex-wrap gap-2">
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

        <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              aria-hidden
            />
            <input
              type="search"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              placeholder="Buscar por empresa ou título…"
              aria-label="Buscar por empresa ou título"
              className="w-full rounded-md border border-border bg-surface py-1.5 pl-9 pr-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none"
            />
          </div>
          <select
            value={ordenacao}
            onChange={(e) => setOrdenacao(e.target.value as Ordenacao)}
            aria-label="Ordenar vagas"
            className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-text focus:border-accent focus:outline-none"
          >
            {ORDENACOES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        {carregando && (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )}

        {!carregando && vagasFiltradas.length === 0 && !erro && (
          <div className="rounded-xl border border-dashed border-border py-14 text-center">
            <p className="text-sm text-muted">
              {busca
                ? `Nenhuma vaga corresponde a "${busca}".`
                : `Nenhuma vaga com status "${STATUS_LABEL[filtro as Status] ?? "todas"}".`}
            </p>
            {!busca && (
              <p className="mt-1 text-xs text-muted">
                Rode <code className="rounded bg-bg px-1.5 py-0.5">triar analisar</code>{" "}
                ou <code className="rounded bg-bg px-1.5 py-0.5">triar buscar</code> para
                popular o histórico.
              </p>
            )}
          </div>
        )}

        {!carregando && vagasFiltradas.length > 0 && (
          <div className="flex flex-col gap-3">
            {vagasFiltradas.map((vaga) => (
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

      <Modal
        title="Como popular o histórico"
        open={ajudaAberta}
        onClose={() => setAjudaAberta(false)}
      >
        <div className="space-y-3 text-sm text-muted">
          <p>
            Esta tela só lê o <code className="rounded bg-bg px-1.5 py-0.5 text-text">historico.json</code> gerado pela CLI. Para popular ou atualizar vagas, rode no terminal:
          </p>
          <div className="space-y-2 rounded-md bg-bg p-3 font-mono text-xs text-text">
            <p>triar buscar --limite 10</p>
            <p>triar analisar vagas.json</p>
          </div>
          <p>
            Trocar o status aqui (Aplicado, Entrevista, ...) grava direto no mesmo
            arquivo usado pela CLI — os dois podem ser usados juntos com segurança.
          </p>
        </div>
      </Modal>
    </div>
  );
}
