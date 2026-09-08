"""Processador durável: python -m api.worker. Cada busca roda em processo isolado."""

import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from .queue import atualizar, expirar_abandonadas, prazo_segundos, reservar_proxima  # noqa: E402

logger = logging.getLogger(__name__)
ACORDAR = threading.Event()


def processar_uma() -> bool:
    expirar_abandonadas()
    reserva = reservar_proxima()
    if not reserva:
        return False
    busca_id, token = reserva
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "api.worker", "--job", busca_id, token],
            timeout=prazo_segundos(), check=False,
            # Não registrar currículo, prompts ou respostas brutas de provedores.
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if resultado.returncode:
            raise RuntimeError("worker_interrompido")
    except (subprocess.TimeoutExpired, OSError, RuntimeError) as erro:
        logger.error("Busca %s interrompida: %s", busca_id, type(erro).__name__)
        atualizar(busca_id, token, estado="falhou", progresso=100,
                  erro="O processamento foi interrompido. Tente novamente mais tarde.",
                  mensagem="Busca interrompida; resultados já salvos foram preservados.",
                  concluida_em=datetime.now(timezone.utc))
    return True


def rodar(parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            if processar_uma():
                continue
        except Exception as erro:  # noqa: BLE001 - falha do banco não mata o consumidor
            logger.error("Fila indisponível: %s", type(erro).__name__)
        ACORDAR.wait(2)
        ACORDAR.clear()


def iniciar_embutido() -> tuple[threading.Event, threading.Thread] | None:
    if os.environ.get("TRIAGEM_WORKER_MODE", "embedded") != "embedded":
        return None
    parar = threading.Event()
    thread = threading.Thread(target=rodar, args=(parar,), daemon=True, name="fila-saas")
    thread.start()
    return parar, thread


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--job":
        raise SystemExit("Argumentos inválidos")
    if len(sys.argv) == 4 and sys.argv[1] == "--job":
        from .processamento import executar_busca
        executar_busca(sys.argv[2], sys.argv[3])
    else:
        from .config import validar_configuracao
        validar_configuracao()
        logging.basicConfig(level=logging.INFO)
        try:
            rodar(threading.Event())
        except KeyboardInterrupt:
            pass
