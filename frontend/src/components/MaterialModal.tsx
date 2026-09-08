import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { gerarMaterial, mensagemErro, obterBusca, obterMaterial } from "../api";
import type { BuscaVagas, VagaResumo } from "../types";
import { Modal } from "./Modal";

export function MaterialModal({ open, onClose, vaga }: { open: boolean; onClose: () => void; vaga: VagaResumo }) {
  const [job, setJob] = useState<BuscaVagas | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [iniciando, setIniciando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [pollTentativa, setPollTentativa] = useState(0);
  useEffect(() => {
    if (!open) return;
    // oxlint-disable-next-line react/set-state-in-effect -- Opening a different job reloads the persisted result.
    let ativo = true; setCarregando(true); setErro(null);
    obterMaterial(vaga.id).then((result) => { if (ativo) setJob(result); })
      .catch((e) => { if (ativo) setErro(mensagemErro(e, "Não foi possível carregar seu material. Tente novamente.")); })
      .finally(() => { if (ativo) setCarregando(false); });
    return () => { ativo = false; };
  }, [open, vaga.id, tentativa]);
  useEffect(() => {
    if (!open || !job || !["pendente", "processando"].includes(job.estado)) return;
    let ativo = true;
    const timer = window.setTimeout(() => {
      obterBusca(job.id).then((result) => { if (ativo) { setJob(result); setErro(null); } })
        .catch((e) => { if (ativo) { setErro(mensagemErro(e, "Não foi possível atualizar o progresso. Tentando novamente…")); setPollTentativa((n) => n + 1); } });
    }, 3000);
    return () => { ativo = false; window.clearTimeout(timer); };
  }, [job, open, pollTentativa]);
  async function gerar() {
    setIniciando(true); setErro(null);
    try { setJob(await gerarMaterial(vaga.id)); }
    catch (e) { setErro(mensagemErro(e, "Não foi possível preparar sua candidatura. Tente novamente mais tarde.")); }
    finally { setIniciando(false); }
  }
  function baixar() {
    if (!job?.resultado) return;
    const url = URL.createObjectURL(new Blob([job.resultado], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `candidatura-${vaga.id.replace(/[^a-zA-Z0-9_-]/g, "_")}.md`;
    document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const emAndamento = !!job && ["pendente", "processando"].includes(job.estado);
  return <Modal title="Preparar candidatura" open={open} onClose={onClose} width="max-w-2xl"><div className="space-y-4 text-sm">
    <div><h3 className="font-semibold">{vaga.titulo}</h3><p className="text-muted">{vaga.empresa}</p></div>
    <p className="text-muted">Prepare um material adaptado à oportunidade usando o conteúdo do seu currículo. Revise todas as informações antes de enviar. A candidatura continua sendo feita por você.</p>
    <p className="text-xs text-muted">Cada geração utiliza 2 unidades do limite mensal de análises. O texto é produzido com IA e pode conter erros.</p>
    {carregando && <p role="status" className="text-muted">Carregando material…</p>}
    {job && <div role="status" className="space-y-2"><p className={job.estado === "falhou" ? "text-danger" : "text-muted"}>{job.erro || job.mensagem}</p>{emAndamento && <progress aria-label="Progresso da candidatura" className="w-full accent-accent" max={100} value={job.progresso} />}</div>}
    {job?.estado === "concluida" && job.resultado && <><label className="block space-y-2 text-muted">Material para revisar<textarea readOnly className="field min-h-72" value={job.resultado} /></label><button onClick={baixar} className="btn-primary inline-flex items-center gap-2"><Download size={16} />Baixar material (.md)</button></>}
    {erro && <p role="alert" className="rounded-md bg-danger/10 p-3 text-danger">{erro}</p>}
    {!carregando && !emAndamento && <button disabled={iniciando} onClick={() => { void gerar(); }} className="btn-primary">{iniciando ? "Iniciando…" : job?.estado === "concluida" ? "Gerar novamente (2 análises)" : "Gerar material (2 análises)"}</button>}
    {erro && <button onClick={() => setTentativa((n) => n + 1)} className="ml-3 inline-flex items-center gap-1 text-accent"><RefreshCw size={14} />Atualizar</button>}
  </div></Modal>;
}
