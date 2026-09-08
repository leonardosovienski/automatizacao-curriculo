"""SSRF, DNS rebinding e protocolo HTTP/TLS: nenhuma conexão externa nos testes."""

import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

import httpcore
import httpx
import pytest

from triagem import buscador, rede_segura


class StreamFalso(httpcore.NetworkStream):
    def __init__(self, resposta=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK", peer="8.8.8.8"):
        self.resposta = resposta
        self.peer = peer
        self.escritas = []
        self.tls = []
        self.fechado = False

    def read(self, max_bytes, timeout=None):
        parte, self.resposta = self.resposta[:max_bytes], self.resposta[max_bytes:]
        return parte

    def write(self, buffer, timeout=None):
        self.escritas.append(buffer)

    def close(self):
        self.fechado = True

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((ssl_context, server_hostname, timeout))
        return self

    def get_extra_info(self, info):
        if info == "server_addr":
            return self.peer, 443
        if info == "is_readable":
            return False
        return None


def dns_publico(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 0))]


@pytest.mark.parametrize("url,metodo,host", [
    ("http://vagas.test/vaga?pagina=1", "GET", "vagas.test"),
    ("https://vagas.test:8443/vaga", "GET", "vagas.test:8443"),
    ("https://vagas.test/vaga", "HEAD", "vagas.test"),
])
def test_transporte_fixa_ip_preserva_host_sni_e_verificacao_tls(monkeypatch, url, metodo, host):
    stream = StreamFalso()
    conectadas = []
    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", dns_publico)

    def conectar(_backend, ip, porta, **kwargs):
        conectadas.append((ip, porta, kwargs))
        return stream

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", conectar)
    # Proxies não podem transferir a resolução do domínio a outro processo.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    resposta = rede_segura.request(metodo, url, timeout=7)
    assert resposta.status_code == 200
    assert resposta.text == ("" if metodo == "HEAD" else "OK")
    assert conectadas[0][0] == "8.8.8.8"
    assert 0 < conectadas[0][2]["timeout"] <= 7
    requisicao = b"".join(stream.escritas)
    assert f"Host: {host}\r\n".encode() in requisicao
    assert requisicao.startswith(metodo.encode() + b" /vaga")
    if url.startswith("https"):
        contexto, sni, timeout = stream.tls[0]
        assert sni == "vagas.test"
        assert contexto.check_hostname and contexto.verify_mode == ssl.CERT_REQUIRED
        assert timeout == 7
    else:
        assert not stream.tls
    assert stream.fechado


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
    "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:0001::", "ff02::1",
])
def test_rejeita_ips_privados_metadados_multicast_e_tuneis(monkeypatch, ip):
    assert not rede_segura.ip_publico(ip)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *_, **__: pytest.fail("IP proibido chegou ao socket"))
    with pytest.raises(httpcore.ConnectError, match="não permitido"):
        rede_segura.BackendPublico().connect_tcp(ip, 80, timeout=1)


def test_dns_misto_inseguro_e_cache_preliminar_nao_autorizam_conexao(monkeypatch):
    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", lambda *_, **__: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ])
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *_, **__: pytest.fail("Resolução mista abriu socket"))
    with pytest.raises(httpx.ConnectError, match="não permitido"):
        rede_segura.get("https://vagas.test/anuncio")


def test_peer_diferente_e_rejeitado_antes_de_enviar_http(monkeypatch):
    stream = StreamFalso(peer="127.0.0.1")
    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", dns_publico)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *_, **__: stream)
    with pytest.raises(httpx.ConnectError, match="IP validado"):
        rede_segura.get("https://vagas.test/anuncio")
    assert stream.fechado and not stream.escritas and not stream.tls


def test_fallback_publico_respeita_prazo_total_e_mapeia_timeout(monkeypatch):
    monkeypatch.setattr(rede_segura, "resolver_publicos", lambda *_: ["8.8.8.8", "1.1.1.1"])
    relogio = iter([100, 101, 102])
    monkeypatch.setattr(rede_segura.time, "monotonic", lambda: next(relogio))
    stream = StreamFalso(peer="1.1.1.1")
    tentativas = []

    def conectar(_backend, ip, _porta, **kwargs):
        tentativas.append((ip, kwargs["timeout"]))
        if ip == "8.8.8.8":
            raise httpcore.ConnectTimeout("Indisponível")
        return stream

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", conectar)
    assert rede_segura.BackendPublico().connect_tcp("vagas.test", 443, timeout=5) is stream
    assert tentativas == [("8.8.8.8", 4), ("1.1.1.1", 3)]
    relogio = iter([100, 110])
    with pytest.raises(httpcore.ConnectTimeout, match="Prazo"):
        rede_segura.BackendPublico().connect_tcp("vagas.test", 443, timeout=5)


def test_erro_dns_e_erro_leitura_seguem_contrato_httpx(monkeypatch):
    def dns_falha(*_, **__):
        raise socket.gaierror("Falha DNS")

    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", dns_falha)
    with pytest.raises(httpx.ConnectError):
        rede_segura.get("https://vagas.test")
    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", dns_publico)

    class LeituraFalha(StreamFalso):
        def read(self, *_args, **_kwargs):
            raise httpcore.ReadTimeout("Prazo de leitura")

    stream = LeituraFalha()
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda *_, **__: stream)
    with pytest.raises(httpx.ReadTimeout):
        rede_segura.get("https://vagas.test")
    assert stream.fechado


def test_redirect_privado_robots_e_roteador_usam_transporte_seguro(monkeypatch):
    # A mesma fronteira TCP é exercitada por robots, GET e HEAD de roteador.
    chamadas = []
    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", dns_publico)
    monkeypatch.setattr(buscador, "_esperar_vez", lambda *_: None)
    buscador._robots.cache_clear()
    buscador._host_e_seguro.cache_clear()

    def conectar(*_, **__):
        indice = len(chamadas)
        resposta = (b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n" if indice == 0 else
                    b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/admin\r\nContent-Length: 0\r\n\r\n")
        stream = StreamFalso(resposta)
        chamadas.append(stream)
        return stream

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", conectar)
    try:
        with pytest.raises(httpx.InvalidURL):
            buscador._obter("https://vagas.test/anuncio")
        assert len(chamadas) == 2
        assert b"GET /robots.txt" in b"".join(chamadas[0].escritas)
        assert b"GET /anuncio" in b"".join(chamadas[1].escritas)
        assert buscador._resolver_router("https://vertexaisearch.cloud.google.com/redirect") == "http://127.0.0.1/admin"
        assert b"HEAD /redirect" in b"".join(chamadas[2].escritas)
        # O roteador só resolve o destino; o GET subsequente não pode acessá-lo.
        with pytest.raises(httpx.InvalidURL):
            buscador._obter("http://127.0.0.1/admin")
        assert len(chamadas) == 3
    finally:
        buscador._robots.cache_clear()
        buscador._host_e_seguro.cache_clear()


def test_rebinding_real_nao_alcanca_servidor_local_efemero(monkeypatch):
    recebidas, conexoes, consultas = [], [], []

    class ServidorLocal(ThreadingHTTPServer):
        def server_bind(self):
            TCPServer.server_bind(self)
            self.server_name = "audit.local"
            self.server_port = self.server_address[1]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            recebidas.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    servidor = ServidorLocal(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()

    def rebinding(host, porta, **_kwargs):
        assert host == "audit.invalid"
        ip = "8.8.8.8" if not consultas else "127.0.0.1"
        consultas.append(ip)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, porta or 0))]

    def nenhuma_conexao(_socket, endereco):
        conexoes.append(endereco)
        raise AssertionError("Rebinding chegou à conexão TCP")

    monkeypatch.setattr(rede_segura.socket, "getaddrinfo", rebinding)
    monkeypatch.setattr(socket.socket, "connect", nenhuma_conexao)
    monkeypatch.setattr(buscador, "_permitido_por_robots", lambda *_: True)
    monkeypatch.setattr(buscador, "_esperar_vez", lambda *_: None)
    buscador._host_e_seguro.cache_clear()
    try:
        with pytest.raises(httpx.ConnectError, match="não permitido"):
            buscador._obter(f"http://audit.invalid:{servidor.server_port}/somente-prova")
        assert consultas == ["8.8.8.8", "127.0.0.1"]
        assert not conexoes and not recebidas
        assert buscador._host_e_seguro("audit.invalid")  # cache preliminar continua positivo
    finally:
        buscador._host_e_seguro.cache_clear()
        servidor.shutdown()
        servidor.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://usuario:senha@vagas.test/"])
def test_esquemas_e_credenciais_na_url_rejeitados(url):
    with pytest.raises((httpx.InvalidURL, httpx.UnsupportedProtocol)):
        rede_segura.get(url)


def test_redirect_automatico_nao_pode_burlar_robots():
    with pytest.raises(ValueError, match="Redirecionamentos"):
        rede_segura.get("https://vagas.test/", follow_redirects=True)
