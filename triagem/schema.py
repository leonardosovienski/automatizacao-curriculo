"""Modelos Pydantic usados como structured output da API e no relatório."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Dimensao(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    nota: int = Field(ge=0, le=10)
    justificativa: str = Field(min_length=1)


class Notas(BaseModel):
    d1_crescimento: Dimensao
    d2_regime_localizacao: Dimensao
    d3_stack_fit: Dimensao
    d4_ingles: Dimensao
    d5_nivel_real: Dimensao


class AnaliseVaga(BaseModel):
    """Resposta estruturada do modelo para uma vaga."""

    titulo_normalizado: str
    empresa: str
    regime: Literal["remoto", "hibrido", "presencial"]
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
    """Vaga descoberta na web antes da análise detalhada."""

    titulo: str = Field(min_length=1)
    empresa: str = ""
    descricao: str = Field(min_length=40)
    link: str = Field(pattern=r"^https?://")
    origem: str = ""
    publicada_em: str = ""


class ResultadoBusca(BaseModel):
    vagas: List[VagaEncontrada]
