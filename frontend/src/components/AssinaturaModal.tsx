import { useEffect, useState } from "react";
import { abrirPortalAssinatura, iniciarCheckoutAssinatura, obterStatusAssinatura } from "../api";
import type { StatusAssinatura } from "../types";
import { Modal } from "./Modal";
import { Spinner } from "./Spinner";

const STATUS_LABEL: Record<string, string> = {
  active: "Ativa",
  trialing: "Em teste",
  past_due: "Pagamento pendente",
  canceled: "Cancelada",
  unpaid: "Pagamento em atraso",
  inativa: "Sem assinatura",
};

interface Props {
  open: boolean;
  onClose: () => void;
}

export function AssinaturaModal({ open, onClose }: Props) {
  const [status, setStatus] = useState<StatusAssinatura | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [processando, setProcessando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [indisponivel, setIndisponivel] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCarregando(true);
    setErro(null);
    setIndisponivel(false);
    obterStatusAssinatura()
      .then(setStatus)
      .catch((e) => {
        if (e?.response?.status === 503) setIndisponivel(true);
        else setErro("Não foi possível carregar sua assinatura agora.");
      })
      .finally(() => setCarregando(false));
  }, [open]);

  async function handleAssinar() {
    setProcessando(true);
    setErro(null);
    try {
      const url = await iniciarCheckoutAssinatura();
      window.location.href = url;
    } catch {
      setErro("Não foi possível iniciar o checkout. Tente novamente em instantes.");
      setProcessando(false);
    }
  }

  async function handleGerenciar() {
    setProcessando(true);
    setErro(null);
    try {
      const url = await abrirPortalAssinatura();
      window.location.href = url;
    } catch {
      setErro("Não foi possível abrir o portal de cobrança. Tente novamente em instantes.");
      setProcessando(false);
    }
  }

  return (
    <Modal title="Assinatura" open={open} onClose={onClose}>
      {carregando && (
        <div className="flex items-center justify-center py-8">
          <Spinner />
        </div>
      )}

      {!carregando && indisponivel && (
        <p className="text-sm text-muted">
          A cobrança ainda não está disponível neste ambiente.
        </p>
      )}

      {!carregando && !indisponivel && status && (
        <div className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border border-border bg-bg px-4 py-3">
            <div>
              <p className="text-sm font-medium text-text">
                {STATUS_LABEL[status.status] ?? status.status}
              </p>
              {status.periodo_atual_fim && (
                <p className="text-xs text-muted">
                  Renova em {new Date(status.periodo_atual_fim).toLocaleDateString("pt-BR")}
                </p>
              )}
            </div>
            <span
              className={`h-2.5 w-2.5 rounded-full ${status.ativa ? "bg-emerald-500" : "bg-muted"}`}
              aria-hidden
            />
          </div>

          {status.ativa ? (
            <button
              onClick={handleGerenciar}
              disabled={processando}
              className="w-full rounded-md border border-border px-4 py-2 text-sm font-medium text-text hover:border-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {processando ? "Abrindo…" : "Gerenciar assinatura"}
            </button>
          ) : (
            <button
              onClick={handleAssinar}
              disabled={processando}
              className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg disabled:cursor-not-allowed disabled:opacity-50"
            >
              {processando ? "Redirecionando…" : "Assinar agora"}
            </button>
          )}
        </div>
      )}

      {erro && <p className="mt-3 text-xs text-red-400">{erro}</p>}
    </Modal>
  );
}
