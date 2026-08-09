"""Perfil configurável do candidato e persistência do onboarding."""

import os
import tempfile
from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel, Field, field_validator


class PerfilUsuario(BaseModel):
    versao: int = 1
    nome: str = "Leo"
    pais: str = "Brasil"
    cidades_aceitas: List[str] = Field(
        default_factory=lambda: [
            "Curitiba", "Araucária", "São José dos Pinhais", "Colombo", "Pinhais"
        ]
    )
    aceita_remoto: bool = True
    aceita_hibrido: bool = True
    aceita_presencial: bool = True
    areas: List[str] = Field(
        default_factory=lambda: [
            "DevOps", "DevSecOps", "Platform Engineer", "SRE", "Cloud", "C#", ".NET"
        ]
    )
    senioridades: List[str] = Field(
        default_factory=lambda: ["Estágio", "Trainee", "Júnior", "Jr", "Entry Level"]
    )
    tecnologias: List[str] = Field(default_factory=list)
    idiomas: List[str] = Field(default_factory=lambda: ["Português", "Inglês"])
    pesos: Dict[str, float] = Field(default_factory=lambda: {
        "d1_crescimento": 0.30,
        "d2_regime_localizacao": 0.25,
        "d3_stack_fit": 0.20,
        "d4_ingles": 0.15,
        "d5_nivel_real": 0.10,
    })
    cv_base: str = "perfil/cv_base.md"
    consentimento_ia: bool = False
    onboarding_concluido: bool = False

    @field_validator("nome", "pais")
    @classmethod
    def nao_vazio(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("não pode ficar vazio")
        return valor

    @field_validator("areas", "senioridades", "cidades_aceitas")
    @classmethod
    def lista_sem_vazios(cls, valores: List[str]) -> List[str]:
        limpos = list(dict.fromkeys(v.strip() for v in valores if v.strip()))
        if not limpos:
            raise ValueError("informe ao menos um valor")
        return limpos

    @field_validator("pesos")
    @classmethod
    def pesos_validos(cls, pesos: Dict[str, float]) -> Dict[str, float]:
        esperados = {"d1_crescimento", "d2_regime_localizacao", "d3_stack_fit", "d4_ingles", "d5_nivel_real"}
        if set(pesos) != esperados or any(not 0 <= p <= 1 for p in pesos.values()):
            raise ValueError("pesos devem conter D1-D5, com valores entre 0 e 1")
        if abs(sum(pesos.values()) - 1.0) > 1e-6:
            raise ValueError("os pesos devem somar 1.0")
        return pesos

    def pedido_padrao(self) -> str:
        areas = ", ".join(self.areas)
        niveis = ", ".join(self.senioridades)
        locais = ", ".join(self.cidades_aceitas)
        modalidades = []
        if self.aceita_remoto:
            modalidades.append(f"remotas em {self.pais}")
        if self.aceita_hibrido:
            modalidades.append(f"híbridas em {locais}")
        if self.aceita_presencial:
            modalidades.append(f"presenciais em {locais}")
        return f"Vagas de {niveis} em {areas}, " + " ou ".join(modalidades)

    def bloco_prompt(self) -> str:
        return (
            "# CONFIGURAÇÃO ATIVA DO CANDIDATO — SOBRESCREVE EXEMPLOS DO TEMPLATE\n"
            f"- Nome: {self.nome}\n- País: {self.pais}\n"
            f"- Cidades/raio aceitos: {', '.join(self.cidades_aceitas)}\n"
            f"- Áreas/cargos: {', '.join(self.areas)}\n"
            f"- Senioridades aceitas: {', '.join(self.senioridades)}\n"
            f"- Tecnologias do perfil: {', '.join(self.tecnologias) or 'usar o CV base'}\n"
            f"- Modalidades: remoto={'sim' if self.aceita_remoto else 'não'}, "
            f"híbrido={'sim' if self.aceita_hibrido else 'não'}, "
            f"presencial={'sim' if self.aceita_presencial else 'não'}\n"
            "Qualquer nome, cidade, área ou senioridade diferente citado abaixo é somente "
            "exemplo legado e NÃO substitui esta configuração.\n\n"
        )


PADRAO = Path.cwd() / "perfil.json"
ARQUIVO = Path(os.environ.get("TRIAGEM_PERFIL") or PADRAO)
_ATIVO = PerfilUsuario()


def aplicar_config_do_ambiente() -> None:
    global ARQUIVO
    ARQUIVO = Path(os.environ.get("TRIAGEM_PERFIL") or PADRAO)


def carregar() -> PerfilUsuario:
    global _ATIVO
    if ARQUIVO.exists():
        try:
            _ATIVO = PerfilUsuario.model_validate_json(ARQUIVO.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"Perfil inválido em '{ARQUIVO}': {e}") from e
    else:
        _ATIVO = PerfilUsuario()
    return _ATIVO


def atual() -> PerfilUsuario:
    return _ATIVO


def salvar(perfil: PerfilUsuario) -> None:
    global _ATIVO
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    fd, temporario = tempfile.mkstemp(prefix=f".{ARQUIVO.name}.", suffix=".tmp", dir=ARQUIVO.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            arquivo.write(perfil.model_dump_json(indent=2))
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, ARQUIVO)
    except BaseException:
        try:
            os.unlink(temporario)
        except FileNotFoundError:
            pass
        raise
    _ATIVO = perfil
