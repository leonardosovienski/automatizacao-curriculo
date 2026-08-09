import { useState } from "react";
import { cadastrar, entrar } from "../api";

export function AuthScreen({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [cadastro, setCadastro] = useState(false);
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault(); setCarregando(true); setErro(null);
    try {
      if (cadastro) await cadastrar(email, senha); else await entrar(email, senha);
      onAuthenticated();
    } catch (e) {
      setErro((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Não foi possível autenticar.");
    } finally { setCarregando(false); }
  }

  return <main className="flex min-h-screen items-center justify-center px-4">
    <form onSubmit={enviar} className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-surface p-6">
      <div><h1 className="text-xl font-semibold text-text">Triagem de Vagas</h1><p className="mt-1 text-sm text-muted">{cadastro ? "Crie sua conta" : "Entre na sua conta"}</p></div>
      <label className="block space-y-1 text-sm text-muted">E-mail<input required type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className="w-full rounded-md border border-border bg-bg px-3 py-2 text-text focus:border-accent focus:outline-none" /></label>
      <label className="block space-y-1 text-sm text-muted">Senha<input required minLength={cadastro ? 10 : undefined} type="password" autoComplete={cadastro ? "new-password" : "current-password"} value={senha} onChange={(e) => setSenha(e.target.value)} className="w-full rounded-md border border-border bg-bg px-3 py-2 text-text focus:border-accent focus:outline-none" />{cadastro && <span className="text-xs">Mínimo de 10 caracteres.</span>}</label>
      {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-sm text-danger">{erro}</p>}
      <button disabled={carregando} className="w-full rounded-md bg-accent px-4 py-2 font-medium text-white disabled:opacity-50">{carregando ? "Aguarde…" : cadastro ? "Criar conta" : "Entrar"}</button>
      <button type="button" onClick={() => { setCadastro(!cadastro); setErro(null); }} className="w-full text-sm text-accent">{cadastro ? "Já tenho uma conta" : "Criar uma conta"}</button>
    </form>
  </main>;
}
