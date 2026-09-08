import axios from "axios";
import type { BuscaVagas, EstadoOnboarding, PerfilUsuario, Sessao, StatusAssinatura, Stats, Status, UsuarioSessao, VagaResumo } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

const client = axios.create({ baseURL: BASE_URL, withCredentials: true, timeout: 30_000 });
client.interceptors.response.use((response) => response, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event("sessao-expirada"));
  }
  return Promise.reject(error);
});

export function mensagemErro(error: unknown, fallback: string): string {
  if (!axios.isAxiosError(error)) return fallback;
  const detalhe: unknown = error.response?.data?.detail;
  if (typeof detalhe === "string" && error.response && error.response.status < 500) return detalhe;
  if (!error.response) return "Não foi possível conectar. Confira sua conexão e tente novamente.";
  return fallback;
}

export function statusErro(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

export async function cadastrar(email: string, senha: string, aceite_termos = false): Promise<Sessao> {
  const { data } = await client.post<Sessao>("/api/auth/cadastro", { email, senha, aceite_termos });
  return data;
}
export async function entrar(email: string, senha: string): Promise<Sessao> {
  const { data } = await client.post<Sessao>("/api/auth/login", { email, senha });
  return data;
}
export async function encerrarSessao(): Promise<void> { await client.post("/api/auth/logout"); }
export async function obterUsuario(): Promise<UsuarioSessao> {
  const { data } = await client.get<UsuarioSessao>("/api/auth/me"); return data;
}

export async function listarVagas(status?: Status): Promise<VagaResumo[]> {
  const { data } = await client.get<VagaResumo[]>("/api/vagas", {
    params: status ? { status } : undefined,
  });
  return data;
}

export async function obterStats(): Promise<Stats> {
  const { data } = await client.get<Stats>("/api/stats");
  return data;
}

export async function atualizarStatus(
  id: string,
  status: Status,
): Promise<VagaResumo> {
  const { data } = await client.patch<VagaResumo>(`/api/vagas/${id}/status`, {
    status,
  });
  return data;
}

export async function obterOnboarding(): Promise<EstadoOnboarding> {
  const { data } = await client.get<EstadoOnboarding>("/api/onboarding");
  return data;
}

export async function obterPerfil(): Promise<PerfilUsuario> {
  const { data } = await client.get<PerfilUsuario>("/api/perfil");
  return data;
}

export async function salvarPerfil(perfil: PerfilUsuario): Promise<PerfilUsuario> {
  const { data } = await client.put<PerfilUsuario>("/api/perfil", perfil);
  return data;
}

export async function obterCV(): Promise<string> {
  const { data } = await client.get<{ conteudo: string }>("/api/cv");
  return data.conteudo;
}

export async function salvarCV(conteudo: string): Promise<void> {
  await client.put("/api/cv", { conteudo });
}

export async function iniciarBusca(pedido: string, limite = 10): Promise<BuscaVagas> {
  const { data } = await client.post<BuscaVagas>("/api/buscas", { pedido: pedido.trim() || null, limite });
  return data;
}
export async function obterBuscaAtual(): Promise<BuscaVagas | null> {
  const { data } = await client.get<BuscaVagas | null>("/api/buscas/atual"); return data;
}
export async function obterBusca(id: string): Promise<BuscaVagas> {
  const { data } = await client.get<BuscaVagas>(`/api/buscas/${id}`); return data;
}

export async function obterStatusAssinatura(): Promise<StatusAssinatura> {
  const { data } = await client.get<StatusAssinatura>("/billing/status");
  return data;
}
export async function iniciarCheckoutAssinatura(): Promise<string> {
  const { data } = await client.post<{ url: string }>("/billing/checkout");
  return data.url;
}
export async function abrirPortalAssinatura(): Promise<string> {
  const { data } = await client.post<{ url: string }>("/billing/portal");
  return data.url;
}

export async function obterMaterial(id: string): Promise<BuscaVagas | null> {
  const { data } = await client.get<BuscaVagas | null>(`/api/vagas/${encodeURIComponent(id)}/material`); return data;
}
export async function gerarMaterial(id: string): Promise<BuscaVagas> {
  const { data } = await client.post<BuscaVagas>(`/api/vagas/${encodeURIComponent(id)}/material`); return data;
}

export async function sincronizarAssinatura(): Promise<StatusAssinatura> {
  const { data } = await client.post<StatusAssinatura>("/billing/sincronizar"); return data;
}

export async function recuperarSenha(email: string): Promise<string> {
  const { data } = await client.post<{ mensagem: string }>("/api/auth/recuperar-senha", { email }); return data.mensagem;
}

export async function redefinirSenha(token: string, senha: string): Promise<string> {
  const { data } = await client.post<{ mensagem: string }>("/api/auth/redefinir-senha", { token, senha }); return data.mensagem;
}

export async function exportarDados(): Promise<Blob> {
  const { data } = await client.get<Blob>("/api/auth/exportar", { responseType: "blob" }); return data;
}

export async function excluirConta(senha: string): Promise<void> {
  await client.delete("/api/auth/me", { data: { senha } });
}

export interface ConfigPublica { nome_servico: string; suporte_email: string | null; termos_url: string | null; privacidade_url: string | null }
export async function obterConfigPublica(): Promise<ConfigPublica> {
  const { data } = await client.get<ConfigPublica>("/api/config/publico"); return data;
}
