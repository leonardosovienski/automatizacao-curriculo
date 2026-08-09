import { useEffect, useState } from "react";
import { obterCV, obterPerfil, salvarCV, salvarPerfil } from "../api";
import type { PerfilUsuario } from "../types";
import { Modal } from "./Modal";

interface Props {
  open: boolean;
  obrigatorio?: boolean;
  onClose: () => void;
  onComplete: () => void;
}

const csv = (valor: string) => valor.split(",").map((v) => v.trim()).filter(Boolean);
const inputClass = "w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text focus:border-accent focus:outline-none";

export function OnboardingWizard({ open, obrigatorio = false, onClose, onComplete }: Props) {
  const [perfil, setPerfil] = useState<PerfilUsuario | null>(null);
  const [cv, setCV] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    Promise.all([obterPerfil(), obterCV()])
      .then(([p, conteudo]) => {
        setPerfil(p);
        setCV(conteudo || `# CV base — ${p.nome}\n\n## Contato\n<!-- PRIVADO -->\n- E-mail:\n- Telefone:\n<!-- /PRIVADO -->\n\n## Resumo profissional\n\n## Experiência\n\n## Formação\n\n## Projetos\n\n## Competências\n`);
      })
      .catch(() => setErro("Não foi possível carregar a configuração."));
  }, [open]);

  function alterar<K extends keyof PerfilUsuario>(campo: K, valor: PerfilUsuario[K]) {
    setPerfil((p) => (p ? { ...p, [campo]: valor } : p));
  }

  async function concluir() {
    if (!perfil) return;
    if (!perfil.nome.trim() || !perfil.areas.length || !perfil.senioridades.length || !perfil.cidades_aceitas.length) {
      setErro("Preencha nome, áreas, senioridades e ao menos uma cidade.");
      return;
    }
    if (!cv.trim()) {
      setErro("Preencha o CV base.");
      return;
    }
    setSalvando(true);
    setErro(null);
    try {
      await salvarCV(cv);
      await salvarPerfil({ ...perfil, onboarding_concluido: true });
      onComplete();
      onClose();
    } catch (e) {
      const mensagem = (e as { response?: { data?: { detail?: string } }; message?: string }).response?.data?.detail;
      setErro(mensagem || (e as Error).message || "Não foi possível salvar o onboarding.");
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Modal title={obrigatorio ? "Configure seu perfil" : "Configurações"} open={open} onClose={obrigatorio ? () => undefined : onClose}>
      {!perfil ? <p className="text-sm text-muted">Carregando…</p> : (
        <div className="max-h-[75vh] space-y-5 overflow-y-auto pr-1 text-sm">
          <section className="space-y-3">
            <h3 className="font-semibold text-text">1. Objetivo profissional</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-muted">Nome<input className={inputClass} value={perfil.nome} onChange={(e) => alterar("nome", e.target.value)} /></label>
              <label className="space-y-1 text-muted">País<input className={inputClass} value={perfil.pais} onChange={(e) => alterar("pais", e.target.value)} /></label>
            </div>
            <label className="block space-y-1 text-muted">Áreas/cargos, separados por vírgula<input className={inputClass} value={perfil.areas.join(", ")} onChange={(e) => alterar("areas", csv(e.target.value))} /></label>
            <label className="block space-y-1 text-muted">Senioridades<input className={inputClass} value={perfil.senioridades.join(", ")} onChange={(e) => alterar("senioridades", csv(e.target.value))} /></label>
            <label className="block space-y-1 text-muted">Cidades aceitas<input className={inputClass} value={perfil.cidades_aceitas.join(", ")} onChange={(e) => alterar("cidades_aceitas", csv(e.target.value))} /></label>
            <label className="block space-y-1 text-muted">Tecnologias<input className={inputClass} value={perfil.tecnologias.join(", ")} onChange={(e) => alterar("tecnologias", csv(e.target.value))} /></label>
            <div className="flex flex-wrap gap-4 text-muted">
              {([['aceita_remoto','Remoto'],['aceita_hibrido','Híbrido'],['aceita_presencial','Presencial']] as const).map(([campo, label]) => (
                <label key={campo} className="flex items-center gap-2"><input type="checkbox" checked={perfil[campo]} onChange={(e) => alterar(campo, e.target.checked)} />{label}</label>
              ))}
            </div>
          </section>

          <section className="space-y-2">
            <h3 className="font-semibold text-text">2. Currículo-base</h3>
            <p className="text-xs text-muted">Use os marcadores PRIVADO para contato, documentos e endereço. Esses blocos não são enviados à IA.</p>
            <textarea className={`${inputClass} min-h-56 font-mono text-xs`} value={cv} onChange={(e) => setCV(e.target.value)} />
          </section>

          <section className="space-y-3">
            <h3 className="font-semibold text-text">3. Privacidade e IA</h3>
            <p className="text-xs text-muted">As integrações são operadas pelo serviço. Você nunca precisa fornecer chaves de API.</p>
            <label className="flex items-start gap-2 text-muted"><input className="mt-1" type="checkbox" checked={perfil.consentimento_ia} onChange={(e) => alterar("consentimento_ia", e.target.checked)} /><span>Autorizo o envio do CV sem blocos PRIVADO e dos textos das vagas ao Gemini para análise.</span></label>
          </section>

          {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-danger">{erro}</p>}
          <div className="flex justify-end gap-2 border-t border-border pt-4">
            {!obrigatorio && <button className="rounded-md border border-border px-4 py-2 text-muted" onClick={onClose}>Cancelar</button>}
            <button disabled={salvando} className="rounded-md bg-accent px-4 py-2 font-medium text-white disabled:opacity-50" onClick={concluir}>{salvando ? "Validando e salvando…" : "Concluir configuração"}</button>
          </div>
        </div>
      )}
    </Modal>
  );
}
