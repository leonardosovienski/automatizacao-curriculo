"""HTTP para URLs não confiáveis, com DNS fixado na conexão e TLS pelo hostname.

O hostname continua na URL/Host/SNI. Apenas o backend TCP recebe o IP validado;
ele nunca volta a resolver o hostname nem utiliza proxies do ambiente. Usa as
interfaces públicas NetworkBackend/ConnectionPool do httpcore e BaseTransport
do HTTPX, sem modificar DNS global ou atributos privados dos clientes.
"""

import ipaddress
import socket
import time
from contextlib import contextmanager

import httpcore
import httpx


def ip_publico(valor: str) -> bool:
    try:
        endereco = ipaddress.ip_address(valor)
        if not endereco.is_global or endereco.is_multicast or "%" in valor:
            return False
        if isinstance(endereco, ipaddress.IPv6Address):
            if endereco.ipv4_mapped:
                return ip_publico(str(endereco.ipv4_mapped))
            if endereco.sixtofour:
                return ip_publico(str(endereco.sixtofour))
            if endereco.teredo:
                return all(ip_publico(str(ip)) for ip in endereco.teredo)
            if endereco in ipaddress.ip_network("64:ff9b::/96"):
                return ip_publico(str(ipaddress.IPv4Address(int(endereco) & 0xFFFFFFFF)))
        return True
    except ValueError:
        return False


def resolver_publicos(host: str, port: int) -> list[str]:
    """Valida TODOS os A/AAAA de uma resolução, sem cache de autorizações."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            respostas = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as erro:
            raise httpcore.ConnectError("Não foi possível resolver o destino.") from erro
        enderecos = list(dict.fromkeys(resposta[4][0] for resposta in respostas))
    else:
        enderecos = [str(literal)]
    if not enderecos or not all(ip_publico(ip) for ip in enderecos):
        raise httpcore.ConnectError("Destino de rede não permitido.")
    return enderecos


class BackendPublico(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        inicio = time.monotonic()
        enderecos = resolver_publicos(host, port)
        ultimo = None
        for ip in enderecos:
            restante = None if timeout is None else timeout - (time.monotonic() - inicio)
            if restante is not None and restante <= 0:
                raise httpcore.ConnectTimeout("Prazo de conexão excedido.")
            try:
                stream = super().connect_tcp(
                    ip, port, timeout=restante, local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as erro:
                ultimo = erro
                continue
            # Defesa adicional: o peer real deve ser exatamente o IP selecionado.
            try:
                peer = stream.get_extra_info("server_addr")
                if not peer or not ip_publico(peer[0]) or ipaddress.ip_address(peer[0]) != ipaddress.ip_address(ip):
                    raise httpcore.ConnectError("Destino conectado não corresponde ao IP validado.")
            except Exception:
                stream.close()
                raise
            return stream
        raise ultimo or httpcore.ConnectError("Nenhum endereço público disponível.")


@contextmanager
def _erros_httpx():
    try:
        yield
    except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError,
            httpcore.ProxyError, httpcore.UnsupportedProtocol) as erro:
        # As famílias e subclasses públicas têm os mesmos nomes nos dois SDKs.
        classe = getattr(httpx, type(erro).__name__, httpx.RequestError)
        raise classe(str(erro)) from erro


class _CorpoResposta(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        with _erros_httpx():
            yield from self.stream

    def close(self):
        with _erros_httpx():
            self.stream.close()


class TransportePublico(httpx.BaseTransport):
    def __init__(self):
        self.pool = httpcore.ConnectionPool(network_backend=BackendPublico())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in {"http", "https"} or request.url.userinfo:
            raise httpx.InvalidURL("URL de rede não permitida.")
        requisicao = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        with _erros_httpx():
            resposta = self.pool.handle_request(requisicao)
        return httpx.Response(resposta.status, headers=resposta.headers,
                              stream=_CorpoResposta(resposta.stream), extensions=resposta.extensions)

    def close(self):
        self.pool.close()


def request(method: str, url: str, *, timeout: float = 15, headers=None,
            follow_redirects: bool = False) -> httpx.Response:
    # Redirecionamentos são controlados pelo coletor para respeitar robots e os
    # limites de saltos. Cada conexão nova revalida DNS, inclusive no mesmo host.
    if follow_redirects:
        raise ValueError("Redirecionamentos precisam ser validados pelo coletor.")
    with httpx.Client(transport=TransportePublico(), trust_env=False, timeout=timeout) as cliente:
        return cliente.request(method, url, headers=headers, follow_redirects=False)


def get(url: str, **kwargs) -> httpx.Response:
    return request("GET", url, **kwargs)


def head(url: str, **kwargs) -> httpx.Response:
    return request("HEAD", url, **kwargs)
