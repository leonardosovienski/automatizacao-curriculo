import type { ConfigPublica } from "../api";

export function LegalLinks({ config }: { config: ConfigPublica | null }) {
  return <nav aria-label="Informações do serviço" className="flex flex-wrap justify-center gap-x-5 gap-y-2 text-xs text-muted">
    {config?.termos_url && <a href={config.termos_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4 hover:text-text">Termos de uso</a>}
    {config?.privacidade_url && <a href={config.privacidade_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4 hover:text-text">Política de privacidade</a>}
    {config?.suporte_email && <a href={`mailto:${config.suporte_email}`} className="underline underline-offset-4 hover:text-text">Falar com suporte</a>}
  </nav>;
}
