export type Status =
  | "novo"
  | "aplicado"
  | "entrevista"
  | "recusado"
  | "descartada"
  | "fechada";

export interface Dimensao {
  nota: number;
  justificativa: string;
}

export interface VagaResumo {
  id: string;
  empresa: string;
  titulo: string;
  status: Status;
  score_final: number | null;
  regime: string;
  localizacao: string;
  nivel_real: string;
  idioma_trabalho: string;
  analisado_em: string;
  link: string | null;
  stack_exigida: string[];
  stack_desejavel: string[];
  alertas: string[];
  motivo_descarte: string | null;
  notas: Record<string, Dimensao> | null;
}

export interface Stats {
  total: number;
  por_status: Record<Status, number>;
}

export const STATUS_LABEL: Record<Status, string> = {
  novo: "Novo",
  aplicado: "Aplicado",
  entrevista: "Entrevista",
  recusado: "Recusado",
  descartada: "Descartada",
  fechada: "Fechada",
};

export const STATUS_EDITAVEIS: Status[] = [
  "novo",
  "aplicado",
  "entrevista",
  "recusado",
  "fechada",
];

export const DIMENSAO_LABEL: Record<string, string> = {
  d1_crescimento: "Crescimento",
  d2_regime_localizacao: "Regime / Localização",
  d3_stack_fit: "Stack fit",
  d4_ingles: "Inglês",
  d5_nivel_real: "Nível real",
};

export const REGIME_LABEL: Record<string, string> = {
  remoto: "Remoto",
  hibrido: "Híbrido",
  presencial: "Presencial",
  indefinido: "Regime indefinido",
};

export const NIVEL_LABEL: Record<string, string> = {
  estagio: "Estágio",
  jr: "Júnior",
  pleno_disfarcado: "Pleno (disfarçado)",
  senior: "Sênior",
};
