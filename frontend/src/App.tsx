import { CheckCircle2, CreditCard, HelpCircle, Search, Settings } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { atualizarStatus, encerrarSessao, iniciarBusca, listarVagas, obterBusca, obterBuscaAtual, obterOnboarding, obterStats, obterUsuario } from "./api";
import { AssinaturaModal } from "./components/AssinaturaModal";
import { AuthScreen } from "./components/AuthScreen";
import { CardSkeleton } from "./components/CardSkeleton";
import { Modal } from "./components/Modal";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { StatsBar } from "./components/StatsBar";
import { Toast } from "./components/Toast";
import { VagaCard } from "./components/VagaCard";
import { STATUS_LABEL, type BuscaVagas, type Stats, type Status, type VagaResumo } from "./types";

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

function Dashboard({ onLogout }: { onLogout: () => void }) {
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
  const [configAberta, setConfigAberta] = useState(false);
  const [assinaturaAberta, setAssinaturaAberta] = useState(false);
  const [configObrigatoria, setConfigObrigatoria] = useState(false);
  const [pedidoBusca, setPedidoBusca] = useState("");
  const [buscaAtual, setBuscaAtual] = useState<BuscaVagas | null>(null);
  const [iniciandoBusca, setIniciandoBusca] = useState(false);
  const [recarregar, setRecarregar] = useState(0);

  useEffect(() => {
    obterOnboarding()
      .then((estado) => {
        if (!estado.concluido) {
          setConfigObrigatoria(true);
          setConfigAberta(true);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    obterBuscaAtual().then(setBuscaAtual).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!buscaAtual || !["pendente", "processando"].includes(buscaAtual.estado)) return;
    const timer = window.setTimeout(() => {
      obterBusca(buscaAtual.id).then((atualizada) => {
        setBuscaAtual(atualizada);
        if (atualizada.estado === "concluida") setRecarregar((n) => n + 1);
      }).catch(() => setErro("Não foi possível acompanhar a busca de vagas."));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [buscaAtual]);

  useEffect(() => {
    // Mesma guarda do efeito de vagas abaixo: sem ela, uma resposta lenta de
    // /api/stats pode chegar depois de uma troca de status e sobrescrever o
    // dashboard com números já vencidos.
    let cancelado = false;
    obterStats()
      .then((s) => {
        if (cancelado) return;
        setStats(s);
        setStatsErro(false);
      })
      .catch(() => {
        if (!cancelado) setStatsErro(true);
      });
    return () => {
      cancelado = true;
    };
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
  }, [filtro, recarregar]);

  async function handleIniciarBusca() {
    setIniciandoBusca(true); setErro(null);
    try { setBuscaAtual(await iniciarBusca(pedidoBusca)); }
    catch (e: unknown) {
      const detalhe = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setErro(detalhe ?? "Não foi possível iniciar a busca. Confira seu perfil e tente novamente.");
    } finally { setIniciandoBusca(false); }
  }

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
            onClick={async () => { await encerrarSessao(); onLogout(); }}
            className="rounded-md border border-border px-2 py-1 text-xs text-muted hover:text-text"
          >Sair</button>
          <button
            onClick={() => { setConfigObrigatoria(false); setConfigAberta(true); }}
            aria-label="Configurações do perfil"
            className="p-1.5 text-muted transition-colors hover:text-text"
          >
            <Settings size={20} />
          </button>
          <button
            onClick={() => setAssinaturaAberta(true)}
            aria-label="Assinatura"
            className="p-1.5 text-muted transition-colors hover:text-text"
          >
            <CreditCard size={20} />
          </button>
          <button
            onClick={() => setAjudaAberta(true)}
            aria-label="Como buscar vagas"
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

        <section className="mb-6 rounded-xl border border-border bg-surface p-4">
          <h2 className="text-sm font-semibold text-text">Encontrar novas vagas</h2>
          <p className="mt-1 text-xs text-muted">Descreva algo específico ou deixe em branco para usar as preferências do perfil.</p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input value={pedidoBusca} onChange={(e) => setPedidoBusca(e.target.value)} placeholder="Ex.: Python júnior remoto" aria-label="Preferências desta busca" className="min-w-0 flex-1 rounded-md border border-border bg-bg px-3 py-2 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none" />
            <button onClick={handleIniciarBusca} disabled={iniciandoBusca || (!!buscaAtual && ["pendente", "processando"].includes(buscaAtual.estado))} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg disabled:cursor-not-allowed disabled:opacity-50">
              {iniciandoBusca ? "Iniciando…" : "Buscar vagas"}
            </button>
          </div>
          {buscaAtual && <div className="mt-3" role="status">
            <div className="mb-1 flex justify-between text-xs text-muted"><span>{buscaAtual.erro ?? buscaAtual.mensagem}</span><span>{buscaAtual.progresso}%</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-bg"><div className={`h-full transition-all ${buscaAtual.estado === "falhou" ? "bg-red-500" : "bg-accent"}`} style={{ width: `${buscaAtual.progresso}%` }} /></div>
          </div>}
        </section>

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
                Use o botão “Buscar vagas” acima para encontrar oportunidades.
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
        title="Como buscar vagas"
        open={ajudaAberta}
        onClose={() => setAjudaAberta(false)}
      >
        <div className="space-y-3 text-sm text-muted">
          <p>Complete seu perfil e currículo nas configurações. Depois, use “Buscar vagas”. O sistema consulta as fontes configuradas pelo operador, analisa as oportunidades e mostra os resultados neste painel.</p>
          <p>Você não precisa instalar nada nem fornecer chaves de API. Pode atualizar cada vaga para Aplicado, Entrevista, Recusado ou Fechada.</p>
        </div>
      </Modal>
      <OnboardingWizard
        open={configAberta}
        obrigatorio={configObrigatoria}
        onClose={() => setConfigAberta(false)}
        onComplete={() => setConfigObrigatoria(false)}
      />
      <AssinaturaModal
        open={assinaturaAberta}
        onClose={() => setAssinaturaAberta(false)}
      />
    </div>
  );
}

export default function App() {
  const [autenticado, setAutenticado] = useState<boolean | null>(null);
  useEffect(() => { obterUsuario().then(() => setAutenticado(true)).catch(() => setAutenticado(false)); }, []);
  if (autenticado === null) return <div className="flex min-h-screen items-center justify-center text-sm text-muted">Carregando…</div>;
  return autenticado
    ? <Dashboard onLogout={() => setAutenticado(false)} />
    : <AuthScreen onAuthenticated={() => setAutenticado(true)} />;
}
