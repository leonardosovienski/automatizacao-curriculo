export type Status =
  | "novo"
  | "aplicado"
  | "entrevista"
  | "recusado"
  | "descartada"
  | "fechada";

export interface VagaResumo {
  id: string;
  empresa: string;
  titulo: string;
  status: Status;
  score_final: number | null;
  regime: string;
  localizacao: string;
  nivel_real: string;
  analisado_em: string;
  link: string | null;
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
