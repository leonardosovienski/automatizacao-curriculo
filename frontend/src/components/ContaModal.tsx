import { useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { excluirConta, exportarDados, mensagemErro, type ConfigPublica } from "../api";
import type { UsuarioSessao } from "../types";
import { Modal } from "./Modal";
import { LegalLinks } from "./LegalLinks";

export function ContaModal({ open, onClose, usuario, config, onDeleted }: { open: boolean; onClose: () => void; usuario: UsuarioSessao; config: ConfigPublica | null; onDeleted: () => void }) {
  const [senha, setSenha] = useState("");
  const [confirmacao, setConfirmacao] = useState(false);
  const [excluindo, setExcluindo] = useState(false);
  const [exportando, setExportando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [sucesso, setSucesso] = useState<string | null>(null);
  function fechar() { if (excluindo) return; setSenha(""); setConfirmacao(false); setErro(null); setSucesso(null); onClose(); }
  async function exportar() {
    setExportando(true); setErro(null);
    try {
      const blob = await exportarDados(); const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = `meus-dados-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSucesso("Seus dados foram preparados para download.");
    } catch (e) { setErro(mensagemErro(e, "Não foi possível exportar seus dados. Tente novamente.")); }
    finally { setExportando(false); }
  }
  async function excluir(event: React.FormEvent) {
    event.preventDefault(); if (!confirmacao) return;
    setExcluindo(true); setErro(null);
    try { await excluirConta(senha); setSenha(""); onDeleted(); }
    catch (e) { setErro(mensagemErro(e, "Não foi possível excluir sua conta agora. Tente novamente ou fale com o suporte.")); }
    finally { setExcluindo(false); }
  }
  return <Modal title="Minha conta e privacidade" open={open} onClose={fechar}>
    <div className="space-y-6 text-sm"><p className="break-all text-muted">Conectado como <strong className="text-text">{usuario.email}</strong></p>
      <section className="space-y-3"><h3 className="font-semibold">Seus dados, com você</h3><p className="text-muted">Baixe uma cópia do seu perfil, currículo e histórico em formato JSON.</p><button disabled={exportando || excluindo} onClick={exportar} className="flex items-center gap-2 rounded-md border border-border px-4 py-2"><Download size={16} />{exportando ? "Preparando…" : "Exportar meus dados"}</button></section>
      <section className="space-y-3 border-t border-border pt-5"><h3 className="font-semibold">Excluir conta</h3><p className="text-muted">A exclusão remove permanentemente seu perfil, currículo e histórico, encerra o acesso e cancela sua assinatura. Não é possível desfazer. Eventuais solicitações de reembolso devem ser tratadas com o suporte conforme os termos do serviço.</p>
        <form onSubmit={excluir} className="space-y-3"><label className="block space-y-1 text-muted">Confirme sua senha<input required type="password" autoComplete="current-password" maxLength={128} value={senha} onChange={(e) => setSenha(e.target.value)} className="field" /></label><label className="flex items-start gap-2 text-muted"><input required type="checkbox" checked={confirmacao} onChange={(e) => setConfirmacao(e.target.checked)} className="mt-1" /><span>Entendo que meus dados serão excluídos e quero encerrar minha conta.</span></label><button disabled={excluindo || exportando || !confirmacao || !senha} className="flex items-center gap-2 rounded-md border border-danger/50 px-4 py-2 text-danger disabled:opacity-50"><Trash2 size={16} />{excluindo ? "Excluindo conta…" : "Excluir minha conta definitivamente"}</button></form>
      </section>
      {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-danger">{erro}</p>}{sucesso && <p role="status" className="text-muted">{sucesso}</p>}
      <LegalLinks config={config} />
    </div>
  </Modal>;
}
