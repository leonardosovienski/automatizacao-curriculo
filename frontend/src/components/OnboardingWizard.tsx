import { useEffect, useState } from "react";
import { mensagemErro, obterCV, obterPerfil, salvarCV, salvarPerfil } from "../api";
import type { PerfilUsuario } from "../types";
import { Modal } from "./Modal";

interface Props { open: boolean; obrigatorio?: boolean; onClose: () => void; onComplete: () => void }
const csv = (value: string) => [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
type Lists = "areas" | "senioridades" | "cidades_aceitas" | "tecnologias" | "idiomas";
const listaCampos: { campo: Lists; rotulo: string; exemplo: string }[] = [
  { campo: "areas", rotulo: "Áreas/cargos, separados por vírgula", exemplo: "Desenvolvimento, QA, Análise de dados" },
  { campo: "senioridades", rotulo: "Senioridades", exemplo: "Estágio, Júnior, Pleno" },
  { campo: "cidades_aceitas", rotulo: "Cidades aceitas", exemplo: "São Paulo, Curitiba" },
  { campo: "tecnologias", rotulo: "Tecnologias", exemplo: "Python, Excel, SQL" },
  { campo: "idiomas", rotulo: "Idiomas", exemplo: "Português, Inglês" },
];

export function OnboardingWizard({ open, obrigatorio = false, onClose, onComplete }: Props) {
  const [perfil, setPerfil] = useState<PerfilUsuario | null>(null);
  const [listas, setListas] = useState<Record<Lists, string>>({ areas: "", senioridades: "", cidades_aceitas: "", tecnologias: "", idiomas: "" });
  const [cv, setCV] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState(0);
  useEffect(() => {
    if (!open) return;
    let ativo = true;
    // oxlint-disable-next-line react/set-state-in-effect -- Reopening the editor reloads the saved profile instead of stale unsaved fields.
    setErro(null); setCarregando(true); setPerfil(null);
    Promise.all([obterPerfil(), obterCV()]).then(([p, conteudo]) => {
      if (!ativo) return;
      setPerfil(p); setCV(conteudo);
      setListas({ areas: p.areas.join(", "), senioridades: p.senioridades.join(", "), cidades_aceitas: p.cidades_aceitas.join(", "), tecnologias: p.tecnologias.join(", "), idiomas: p.idiomas.join(", ") });
    }).catch((e) => { if (ativo) setErro(mensagemErro(e, "Não foi possível carregar seu perfil.")); })
      .finally(() => { if (ativo) setCarregando(false); });
    return () => { ativo = false; };
  }, [open, tentativa]);
  function alterar<K extends keyof PerfilUsuario>(campo: K, valor: PerfilUsuario[K]) { setPerfil((p) => p ? { ...p, [campo]: valor } : p); }
  async function concluir(event: React.FormEvent) {
    event.preventDefault();
    if (!perfil) return;
    const atualizado = { ...perfil, ...Object.fromEntries(Object.entries(listas).map(([key, value]) => [key, csv(value)])), onboarding_concluido: true } as PerfilUsuario;
    if (!atualizado.nome.trim() || !atualizado.pais.trim() || !atualizado.areas.length || !atualizado.senioridades.length || !atualizado.cidades_aceitas.length) { setErro("Preencha nome, país, áreas, senioridades e ao menos uma cidade."); return; }
    if (!atualizado.aceita_remoto && !atualizado.aceita_hibrido && !atualizado.aceita_presencial) { setErro("Selecione ao menos uma modalidade de trabalho."); return; }
    if (cv.trim().length < 50) { setErro("Cole seu currículo com ao menos 50 caracteres para permitir uma análise útil."); return; }
    setSalvando(true); setErro(null);
    try { await salvarCV(cv); await salvarPerfil(atualizado); onComplete(); onClose(); }
    catch (e) { setErro(mensagemErro(e, "Não foi possível salvar seu perfil. Tente novamente.")); }
    finally { setSalvando(false); }
  }
  return <Modal title={obrigatorio ? "Configure seu perfil" : "Configurações do perfil"} open={open} onClose={salvando ? () => undefined : onClose} width="max-w-2xl">
    {carregando && <p role="status" className="text-sm text-muted">Carregando seu perfil…</p>}
    {!carregando && !perfil && <div className="space-y-4"><p role="alert" className="text-sm text-danger">{erro}</p><button onClick={() => setTentativa((n) => n + 1)} className="btn-primary">Tentar novamente</button></div>}
    {perfil && <form onSubmit={concluir} className="space-y-6 text-sm">
      {obrigatorio && <p className="text-muted">Conte o que você procura e adicione seu currículo. Você pode ajustar tudo depois, nas configurações.</p>}
      <section className="space-y-3"><h3 className="font-semibold">1. Seu objetivo profissional</h3>
        <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-muted">Nome<input required maxLength={120} autoComplete="name" className="field" value={perfil.nome} onChange={(e) => alterar("nome", e.target.value)} /></label><label className="space-y-1 text-muted">País<input required maxLength={100} autoComplete="country-name" className="field" value={perfil.pais} onChange={(e) => alterar("pais", e.target.value)} /></label></div>
        {listaCampos.map(({ campo, rotulo, exemplo }) => <label key={campo} className="block space-y-1 text-muted">{rotulo}<input required={["areas", "senioridades", "cidades_aceitas"].includes(campo)} maxLength={1000} className="field" placeholder={exemplo} value={listas[campo]} onChange={(e) => setListas((prev) => ({ ...prev, [campo]: e.target.value }))} /></label>)}
        <p className="text-xs text-muted">Separe os itens por vírgulas. Para vagas remotas, informe também sua cidade de referência.</p>
        <fieldset><legend className="mb-2 text-muted">Modalidades de trabalho</legend><div className="flex flex-wrap gap-4">{([['aceita_remoto', 'Remoto'], ['aceita_hibrido', 'Híbrido'], ['aceita_presencial', 'Presencial']] as const).map(([campo, label]) => <label key={campo} className="flex items-center gap-2"><input type="checkbox" checked={perfil[campo]} onChange={(e) => alterar(campo, e.target.checked)} />{label}</label>)}</div></fieldset>
      </section>
      <section className="space-y-2"><h3 className="font-semibold">2. Seu currículo</h3><p id="cv-ajuda" className="text-xs text-muted">Cole suas experiências, formação e competências. Evite incluir CPF, endereço, telefone e outras informações desnecessárias à análise.</p><label className="block space-y-1 text-muted">Currículo-base<textarea required minLength={50} maxLength={50000} aria-describedby="cv-ajuda" className="field min-h-52" placeholder="Resumo profissional, experiências, formação, projetos e competências…" value={cv} onChange={(e) => setCV(e.target.value)} /></label><p className="text-right text-xs text-muted">{cv.length.toLocaleString("pt-BR")} caracteres</p></section>
      <section className="space-y-3"><h3 className="font-semibold">3. Permissão para análise com IA</h3><label className="flex items-start gap-2 text-muted"><input className="mt-1" type="checkbox" checked={perfil.consentimento_ia} onChange={(e) => alterar("consentimento_ia", e.target.checked)} /><span>Autorizo o envio do conteúdo profissional do meu currículo e dos textos das vagas ao Google Gemini para análise.</span></label><p className="text-xs text-muted">Você pode retirar essa permissão aqui a qualquer momento. Novas buscas ficam pausadas enquanto ela estiver desativada. Não inclua informações que você não queira compartilhar com o provedor de IA.</p></section>
      {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-danger">{erro}</p>}
      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4"><button type="button" disabled={salvando} className="rounded-md border border-border px-4 py-2 text-muted" onClick={onClose}>{obrigatorio ? "Configurar depois" : "Cancelar"}</button><button disabled={salvando} className="btn-primary">{salvando ? "Salvando…" : "Concluir configuração"}</button></div>
    </form>}
  </Modal>;
}
