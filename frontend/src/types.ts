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

export interface PerfilUsuario {
  versao: number;
  nome: string;
  pais: string;
  cidades_aceitas: string[];
  aceita_remoto: boolean;
  aceita_hibrido: boolean;
  aceita_presencial: boolean;
  areas: string[];
  senioridades: string[];
  tecnologias: string[];
  idiomas: string[];
  pesos: Record<string, number>;
  cv_base: string;
  consentimento_ia: boolean;
  onboarding_concluido: boolean;
}

export interface EstadoOnboarding {
  concluido: boolean;
  consentimento_ia: boolean;
  cv_configurado: boolean;
}

export interface UsuarioSessao { id: string; email: string }
export interface Sessao { usuario: UsuarioSessao }

export type EstadoBusca = "pendente" | "processando" | "concluida" | "falhou";
export interface BuscaVagas {
  id: string; pedido: string; limite: number; estado: EstadoBusca;
  progresso: number; mensagem: string; erro: string | null;
  encontradas: number; criada_em: string; concluida_em: string | null;
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
