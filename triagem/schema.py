"""Modelos Pydantic usados como structured output da API e no relatório."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Dimensao(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)

    nota: int = Field(ge=0, le=10)
    justificativa: str = Field(min_length=1)


class Notas(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    d1_crescimento: Dimensao
    d2_regime_localizacao: Dimensao
    d3_stack_fit: Dimensao
    d4_ingles: Dimensao
    d5_nivel_real: Dimensao


class AnaliseVaga(BaseModel):
    """Resposta estruturada do modelo para uma vaga."""

    model_config = ConfigDict(strict=True, extra="forbid")

    titulo_normalizado: str
    empresa: str
    # "indefinido" existe para que a regra anti-alucinação seja cumprível. Sem ele o
    # Literal não tinha estado de "não sei", e o modelo era obrigado a escolher uma
    # das três opções para não estourar ValidationError — escolhia "remoto", que é a
    # mais provável em vagas de TI e vale 10/10 na D2. Medido em 2026-07-27: três de
    # três vagas com regime não declarado saíram como remoto, e o próprio modelo
    # documentou a inferência nos alertas ("assumido como remoto por padrão de
    # mercado"). O prompt proibia; o schema tornava a obediência impossível.
    regime: Literal["remoto", "hibrido", "presencial", "indefinido"] = Field(
        ...,
        description=(
            "Modalidade de trabalho. Use 'indefinido' estritamente quando o bloco "
            "autoritativo não declarar o regime e a descrição não o afirmar."
        ),
    )
    localizacao: str
    nivel_real: Literal["estagio", "jr", "pleno_disfarcado", "senior"]
    stack_exigida: List[str]
    stack_desejavel: List[str]
    idioma_trabalho: Literal["pt", "en", "misto"]
    link: str
    origem: Literal["gupy", "indeed", "linkedin", "outro"]
    descartada: bool
    motivo_descarte: Optional[str]
    notas: Optional[Notas]  # null quando descartada
    alertas: List[str]

    @model_validator(mode="after")
    def validar_descarte(self):
        if self.descartada:
            if self.notas is not None:
                raise ValueError("vaga descartada deve ter notas nulas")
            if not self.motivo_descarte or not self.motivo_descarte.strip():
                raise ValueError("vaga descartada deve informar motivo_descarte")
        elif self.notas is None:
            raise ValueError("vaga aprovada deve ter notas")
        return self


class VagaPontuada(BaseModel):
    """Análise + id do histórico + score composto calculado em código.

    score_final = None significa descartada no hard filter.
    """

    id: str = ""
    analise: AnaliseVaga
    score_final: Optional[float] = None  # 0-100


class VagaEncontrada(BaseModel):
    """Vaga descoberta na web antes da análise detalhada.

    `localizacao` guarda a praça declarada pela fonte (campo estruturado da API,
    quando existe). É usada pelo filtro determinístico de localização — o modelo
    já foi visto inventando "remoto" para vagas presenciais em outra cidade.
    """

    titulo: str = Field(min_length=1)
    empresa: str = ""
    descricao: str = Field(min_length=40)
    link: str = Field(pattern=r"^https?://")
    origem: str = ""
    publicada_em: str = ""
    localizacao: str = ""
    # URL após seguir os redirects do agregador. É a chave de deduplicação entre
    # fontes: a mesma vaga chega como jooble.org/jdp/N e adzuna.com.br/details/N,
    # mas ambas terminam no anúncio original.
    link_final: str = ""
    # "alta" = veio de campo estruturado da API; "baixa" = o modelo inferiu do
    # texto e não foi possível confirmar no material de origem.
    confianca_empresa: Literal["alta", "media", "baixa"] = "alta"
    # Descrição completa obtida da página do anúncio (Adzuna/Jooble truncam).
    descricao_completa: bool = False
    # Proveniência do ATS descoberta sem LLM; permite Delta Sync no histórico.
    ats_provedor: str = ""
    ats_token: str = ""
    ats_job_id: str = ""

    def chave_dedup(self) -> str:
        return self.link_final or self.link


class ResultadoBusca(BaseModel):
    vagas: List[VagaEncontrada]
