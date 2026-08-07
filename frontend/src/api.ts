import axios from "axios";
import type { Stats, Status, VagaResumo } from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const client = axios.create({ baseURL: BASE_URL });

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
