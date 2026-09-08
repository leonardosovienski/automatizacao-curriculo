import { useEffect, useState } from "react";
import { abrirPortalAssinatura, iniciarCheckoutAssinatura, mensagemErro, obterStatusAssinatura, sincronizarAssinatura } from "../api";
import type { StatusAssinatura } from "../types";
import { Modal } from "./Modal";
import { Spinner } from "./Spinner";

const STATUS_LABEL: Record<string, string> = { active: "Ativa", trialing: "Em teste", past_due: "Pagamento pendente", canceled: "Cancelada", unpaid: "Pagamento em atraso", inativa: "Sem assinatura", incomplete: "Pagamento não concluído", incomplete_expired: "Pagamento expirado", paused: "Pausada" };
interface Props { open: boolean; onClose: () => void; retorno?: "sucesso" | "cancelado" | null; onUpdated?: (status: StatusAssinatura) => void }
function preco(plano: NonNullable<StatusAssinatura["plano"]>) {
  const periodos: Record<string, string> = { month: "mês", year: "ano", week: "semana", day: "dia" };
  const valor = new Intl.NumberFormat("pt-BR", { style: "currency", currency: plano.moeda }).format(plano.valor_centavos / 100);
  return `${valor} / ${plano.intervalo_contagem > 1 ? `${plano.intervalo_contagem} ` : ""}${periodos[plano.intervalo] || plano.intervalo}${plano.intervalo_contagem > 1 ? "s" : ""}`;
}
export function AssinaturaModal({ open, onClose, retorno, onUpdated }: Props) {
  const [status, setStatus] = useState<StatusAssinatura | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [processando, setProcessando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [aguardando, setAguardando] = useState(false);
  useEffect(() => {
    if (!open) return;
    let ativo = true; let timer: number | undefined;
    // oxlint-disable-next-line react/set-state-in-effect -- Every opening or refresh starts an independent billing request.
    setErro(null); setCarregando(true); setStatus(null); setProcessando(false);
    setAguardando(retorno === "sucesso");
    async function carregar(numero = 0) {
      try {
        const atual = numero === 0 && retorno === "sucesso" ? await sincronizarAssinatura() : await obterStatusAssinatura();
        if (!ativo) return;
        setStatus(atual); onUpdated?.(atual);
        if (retorno === "sucesso" && !atual.ativa && numero < 6) timer = window.setTimeout(() => { void carregar(numero + 1); }, 3000);
        else setAguardando(false);
      } catch (e) { if (ativo) { setErro(mensagemErro(e, "Não foi possível carregar sua assinatura agora.")); setAguardando(false); } }
      finally { if (ativo) setCarregando(false); }
    }
    void carregar();
    return () => { ativo = false; window.clearTimeout(timer); };
  }, [open, retorno, tentativa, onUpdated]);
  async function redirecionar(portal: boolean) {
    setProcessando(true); setErro(null);
    try {
      const url = new URL(portal ? await abrirPortalAssinatura() : await iniciarCheckoutAssinatura());
      if (url.protocol !== "https:" || !["checkout.stripe.com", "billing.stripe.com"].includes(url.hostname)) throw new Error("Destino de cobrança inválido");
      window.location.assign(url.href);
    } catch (e) { setErro(mensagemErro(e, "Não foi possível abrir a cobrança. Tente novamente em instantes.")); setProcessando(false); }
  }
  return <Modal title="Seu plano e assinatura" open={open} onClose={onClose}>
    {carregando && <div role="status" className="flex items-center justify-center gap-2 py-8 text-sm text-muted"><Spinner />Carregando assinatura…</div>}
    {!carregando && <div className="space-y-4 text-sm">
      {retorno === "cancelado" && <p role="status" className="rounded-md border border-border p-3 text-muted">O checkout foi encerrado. Você pode continuar quando quiser.</p>}
      {retorno === "sucesso" && <p role="status" className="rounded-md border border-accent/40 p-3">{status?.ativa ? "Assinatura confirmada. Você já pode buscar vagas!" : aguardando ? "Confirmando seu pagamento… Isso pode levar alguns instantes." : "Seu pagamento ainda não foi confirmado. Atualize o status em instantes. Você não precisa pagar novamente."}</p>}
      {status && <>
        <div className="rounded-xl border border-border bg-bg p-4"><p className="text-xs uppercase tracking-wider text-accent">{status.plano?.nome || "Plano de oportunidades"}</p>{status.plano && <p className="mt-2 text-2xl font-semibold">{preco(status.plano)}</p>}<p className="mt-3 font-medium">{STATUS_LABEL[status.status] || "Aguardando atualização"}</p>{status.periodo_atual_fim && <p className="mt-1 text-xs text-muted">Período atual até {new Date(status.periodo_atual_fim).toLocaleDateString("pt-BR")}</p>}</div>
        {status.uso && <div className="space-y-2 rounded-lg border border-border p-4"><h3 className="font-medium">Limites do mês</h3><p className="flex justify-between gap-2 text-muted"><span>Buscas iniciadas</span><span>{status.uso.buscas_utilizadas} / {status.uso.buscas_limite}</span></p><p className="flex justify-between gap-2 text-muted"><span>Vagas reservadas para análise</span><span>{status.uso.analises_reservadas} / {status.uso.analises_limite}</span></p><p className="text-xs text-muted">Os limites reiniciam em {new Date(status.uso.reinicia_em).toLocaleString("pt-BR")}. Cada busca reserva a quantidade máxima solicitada de vagas, inclusive em caso de falha ou menos resultados.</p></div>}
        <ul className="list-inside list-disc space-y-1 text-muted"><li>Busca de oportunidades conforme seu perfil</li><li>Análise de compatibilidade com IA</li><li>Organização e acompanhamento das candidaturas</li></ul>
        {status.configurado === false ? <p className="text-muted">As assinaturas estão temporariamente indisponíveis. Tente novamente mais tarde ou fale com o suporte.</p> : <>
          {!status.ativa && !["past_due", "unpaid", "incomplete", "paused"].includes(status.status) && retorno !== "sucesso" && <button disabled={processando} onClick={() => { void redirecionar(false); }} className="btn-primary w-full">{processando ? "Abrindo checkout…" : "Assinar agora"}</button>}
          {(status.ativa || ["past_due", "unpaid", "incomplete", "paused"].includes(status.status)) && <button disabled={processando} onClick={() => { void redirecionar(true); }} className="w-full rounded-md border border-border px-4 py-2.5 font-medium disabled:opacity-50">{processando ? "Abrindo…" : "Gerenciar assinatura"}</button>}
          <p className="text-xs text-muted">Cobrança recorrente. No portal de pagamento você confere o valor final, acessa faturas, atualiza o pagamento e cancela a renovação. Não há garantia de contratação.</p>
        </>}
      </>}
      {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-danger">{erro}</p>}
      <button disabled={aguardando} onClick={() => setTentativa((n) => n + 1)} className="w-full py-2 text-accent disabled:opacity-50">{aguardando ? "Aguardando confirmação…" : "Atualizar status"}</button>
    </div>}
  </Modal>;
}
