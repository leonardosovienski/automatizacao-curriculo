import { useState } from "react";
import { ArrowRight, CheckCircle2, Search, ShieldCheck } from "lucide-react";
import { cadastrar, entrar, mensagemErro, recuperarSenha, redefinirSenha, type ConfigPublica } from "../api";
import { LegalLinks } from "./LegalLinks";

type Mode = "login" | "cadastro" | "recuperar" | "redefinir";
export function AuthScreen({ onAuthenticated, config, resetToken = null, resetPage = false, aviso }: { onAuthenticated: () => void; config: ConfigPublica | null; resetToken?: string | null; resetPage?: boolean; aviso?: string }) {
  const [mode, setMode] = useState<Mode>(resetPage ? "redefinir" : "login");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [aceite, setAceite] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [mensagem, setMensagem] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);
  const cadastro = mode === "cadastro";
  const novaSenha = cadastro || mode === "redefinir";
  const legalDisponivel = !!(config?.termos_url && config?.privacidade_url);
  function mudarMode(next: Mode) { setMode(next); setErro(null); setMensagem(null); setSenha(""); setConfirmacao(""); }
  async function enviar(evento: React.FormEvent) {
    evento.preventDefault(); setErro(null); setMensagem(null);
    if (novaSenha && senha !== confirmacao) { setErro("As senhas precisam ser iguais."); return; }
    setCarregando(true);
    try {
      if (mode === "recuperar") { setMensagem(await recuperarSenha(email.trim())); return; }
      if (mode === "redefinir") {
        if (!resetToken) { setErro("O link de redefinição está incompleto. Solicite um novo link."); return; }
        const sucesso = await redefinirSenha(resetToken, senha);
        mudarMode("login"); setMensagem(`${sucesso} Entre com sua nova senha.`); return;
      }
      if (cadastro) await cadastrar(email.trim(), senha, aceite); else await entrar(email.trim(), senha);
      onAuthenticated();
    } catch (e) { setErro(mensagemErro(e, "Não foi possível concluir. Tente novamente em instantes.")); }
    finally { setCarregando(false); }
  }
  return <main className="mx-auto grid min-h-screen max-w-5xl items-center gap-10 px-5 py-10 md:grid-cols-2 md:gap-16">
    <section className="space-y-6">
      <div className="flex items-center gap-3"><span className="rounded-xl bg-accent/15 p-3 text-accent"><CheckCircle2 size={24} /></span><h1 className="text-xl font-semibold">{config?.nome_servico || "Triagem de Vagas"}</h1></div>
      <div><p className="mb-3 text-xs font-semibold uppercase tracking-widest text-accent">Sua próxima oportunidade começa aqui</p><h2 className="max-w-md text-3xl font-semibold leading-tight sm:text-4xl">Menos tempo procurando.<br />Mais foco na sua carreira.</h2><p className="mt-4 max-w-md leading-relaxed text-muted">Encontre vagas, compare a compatibilidade com seu perfil e acompanhe suas candidaturas em um só lugar.</p></div>
      <ul className="space-y-3 text-sm text-muted"><li className="flex gap-3"><Search size={18} className="shrink-0 text-accent" />Buscas personalizadas para seus objetivos</li><li className="flex gap-3"><ArrowRight size={18} className="shrink-0 text-accent" />Análises com IA para ajudar você a priorizar</li><li className="flex gap-3"><ShieldCheck size={18} className="shrink-0 text-accent" />Você controla seu currículo e seus dados</li></ul>
      <p className="text-xs text-muted">As análises são sugestões. A candidatura é feita por você no site da vaga; não há garantia de contratação.</p>
    </section>
    <div className="space-y-6">
      <form onSubmit={enviar} className="space-y-4 rounded-2xl border border-border bg-surface p-6 sm:p-8">
        <div><h2 className="text-xl font-semibold">{cadastro ? "Crie sua conta" : mode === "recuperar" ? "Recuperar acesso" : mode === "redefinir" ? "Defina sua nova senha" : "Entre na sua conta"}</h2><p className="mt-2 text-sm text-muted">{cadastro ? "Crie seu perfil e confira o plano antes de assinar." : mode === "recuperar" ? "Enviaremos um link para redefinir sua senha." : mode === "redefinir" ? "Escolha uma senha exclusiva para esta conta." : "Seu painel de oportunidades está esperando."}</p></div>
        {aviso && mode === "login" && <p role="status" className="text-sm text-muted">{aviso}</p>}
        {mode !== "redefinir" && <label className="block space-y-1 text-sm text-muted">E-mail<input required type="email" maxLength={254} autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className="field" /></label>}
        {mode !== "recuperar" && <label className="block space-y-1 text-sm text-muted">{mode === "redefinir" ? "Nova senha" : "Senha"}<input aria-label={mode === "redefinir" ? "Nova senha" : "Senha"} aria-describedby={novaSenha ? "senha-requisitos" : undefined} required minLength={novaSenha ? 10 : undefined} maxLength={128} type="password" autoComplete={novaSenha ? "new-password" : "current-password"} value={senha} onChange={(e) => setSenha(e.target.value)} className="field" />{novaSenha && <span id="senha-requisitos" className="text-xs">De 10 a 128 caracteres.</span>}</label>}
        {novaSenha && <label className="block space-y-1 text-sm text-muted">Confirmar senha<input required minLength={10} maxLength={128} type="password" autoComplete="new-password" value={confirmacao} onChange={(e) => setConfirmacao(e.target.value)} className="field" /></label>}
        {cadastro && legalDisponivel && <label className="flex items-start gap-2 text-sm text-muted"><input type="checkbox" required checked={aceite} onChange={(e) => setAceite(e.target.checked)} className="mt-1" /><span>Li e aceito os <a href={config.termos_url!} target="_blank" rel="noopener noreferrer" className="text-accent underline">termos de uso</a> e li a <a href={config.privacidade_url!} target="_blank" rel="noopener noreferrer" className="text-accent underline">política de privacidade</a>.</span></label>}
        {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-sm text-danger">{erro}</p>}
        {mensagem && <p role="status" className="rounded-md border border-success/40 p-3 text-sm text-text">{mensagem}</p>}
        <button disabled={carregando || (mode === "redefinir" && !resetToken)} className="btn-primary w-full">{carregando ? "Aguarde…" : cadastro ? "Criar conta" : mode === "recuperar" ? "Enviar link de recuperação" : mode === "redefinir" ? "Salvar nova senha" : "Entrar"}</button>
        {mode === "redefinir" && !resetToken && <p role="alert" className="text-sm text-danger">Link inválido ou incompleto. Solicite um novo link de recuperação.</p>}
        {mode === "login" && <button type="button" onClick={() => mudarMode("recuperar")} className="w-full py-1 text-sm text-accent">Esqueci minha senha</button>}
        {mode === "redefinir" && !resetToken && <button type="button" onClick={() => mudarMode("recuperar")} className="w-full py-1 text-sm text-accent">Solicitar novo link</button>}
        <button type="button" disabled={carregando} onClick={() => mudarMode(mode === "login" ? "cadastro" : "login")} className="w-full py-1 text-sm text-accent">{mode === "login" ? "Criar uma conta" : "Já tenho uma conta"}</button>
      </form>
      <LegalLinks config={config} />
    </div>
  </main>;
}
