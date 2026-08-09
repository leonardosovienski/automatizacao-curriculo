import axios from "axios";
import type { BuscaVagas, EstadoOnboarding, PerfilUsuario, Sessao, Stats, Status, UsuarioSessao, VagaResumo } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const client = axios.create({ baseURL: BASE_URL, withCredentials: true });
export async function cadastrar(email: string, senha: string): Promise<Sessao> {
  const { data } = await client.post<Sessao>("/api/auth/cadastro", { email, senha });
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
