"""Conexão com o Spotify (fase 7): troca do código, renovação do token e playlists.

Fluxo (OAuth 2.0 Authorization Code com PKCE, detalhes em docs/modo-foco.md):

1. O app desktop gera o code_verifier (segredo aleatório) e manda ao navegador só o
   code_challenge = SHA-256(verifier). O usuário autoriza no site do Spotify, que
   devolve um `code` para http://127.0.0.1:43821/callback (o próprio app escutando).
2. O app manda code + verifier para POST /spotify/conectar. ESTE módulo troca os dois
   pelos tokens no Spotify (o Spotify confere que SHA-256(verifier) = challenge: quem
   interceptou só o `code` não consegue trocá-lo) e guarda o refresh token CIFRADO.
3. Daí em diante, POST /spotify/token devolve um access token válido (1 h), renovando
   quando falta pouco. O refresh token nunca sai do servidor: conectou uma vez, vale nos
   dois computadores.

Não existe client secret: PKCE foi feito para clientes que não guardam segredo (apps
instalados). O Client ID é público.

O cliente HTTP é injetado (ClienteSpotify(http=...)): nos testes, um
httpx.MockTransport faz o papel do Spotify, sem nenhuma chamada real.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.servicos.cifra import Cifra, ErroCifra

URL_CONTAS = "https://accounts.spotify.com"
URL_API = "https://api.spotify.com/v1"
ESCOPOS = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "playlist-read-private playlist-read-collaborative"
)
# Renova se faltar menos que isto: um token que vence no meio de uma chamada é inútil
MARGEM = timedelta(seconds=120)
# Só loopback (o app no próprio computador): o Spotify recusa localhost e exige o IP
REDIRECT_PERMITIDO = re.compile(r"^http://127\.0\.0\.1:\d{2,5}/callback$")


class ErroSpotify(Exception):
    """Falha ao falar com o Spotify, já traduzida para a resposta da nossa API."""

    def __init__(self, status: int, mensagem: str, retry_after: int | None = None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.retry_after = retry_after


class NaoConectado(Exception):
    pass


@dataclass
class Tokens:
    access_token: str
    expira_em: datetime
    refresh_token: str | None  # o Spotify pode mandar um NOVO a cada renovação
    escopos: str


class ClienteSpotify:
    def __init__(self, client_id: str, http: httpx.Client):
        self.client_id = client_id
        self.http = http

    def trocar_codigo(self, code: str, verifier: str, redirect_uri: str) -> Tokens:
        return self._tokens({
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": self.client_id, "code_verifier": verifier,
        })

    def renovar(self, refresh_token: str) -> Tokens:
        return self._tokens({
            "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self.client_id,
        })

    def perfil(self, access_token: str) -> dict:
        return self._api("GET", "/me", access_token)

    def minhas_playlists(self, access_token: str) -> list[dict]:
        corpo = self._api("GET", "/me/playlists?limit=50", access_token)
        return [
            {
                "uri": p["uri"],
                "nome": p.get("name") or "(sem nome)",
                "dono": (p.get("owner") or {}).get("display_name"),
                "imagem": ((p.get("images") or [{}])[0] or {}).get("url"),
            }
            for p in corpo.get("items", [])
            if p and str(p.get("uri", "")).startswith("spotify:playlist:")
        ]

    def _tokens(self, dados: dict) -> Tokens:
        resposta = self._enviar("POST", f"{URL_CONTAS}/api/token", data=dados)
        if resposta.status_code == 400:
            erro = _json(resposta).get("error")
            if erro == "invalid_grant":
                # Código vencido/reusado, ou o usuário tirou o acesso em spotify.com/account/apps
                raise ErroSpotify(409, "o Spotify recusou a autorização: conecte de novo")
            raise ErroSpotify(502, f"o Spotify recusou o pedido de token ({erro or 400})")
        _conferir(resposta)
        corpo = resposta.json()
        return Tokens(
            access_token=corpo["access_token"],
            expira_em=datetime.now(UTC) + timedelta(seconds=int(corpo.get("expires_in", 3600))),
            refresh_token=corpo.get("refresh_token"),
            escopos=corpo.get("scope", ""),
        )

    def _api(self, metodo: str, caminho: str, access_token: str) -> dict:
        resposta = self._enviar(metodo, f"{URL_API}{caminho}",
                                headers={"Authorization": f"Bearer {access_token}"})
        _conferir(resposta)
        return resposta.json()

    def _enviar(self, metodo: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self.http.request(metodo, url, **kwargs)
        except httpx.HTTPError as erro:
            raise ErroSpotify(502, "não consegui falar com o Spotify agora; tente de novo") from erro


def _json(resposta: httpx.Response) -> dict:
    try:
        corpo = resposta.json()
        return corpo if isinstance(corpo, dict) else {}
    except ValueError:
        return {}


def _razao(corpo: dict) -> str | None:
    """O campo "reason" pode vir no topo ou dentro de "error" ({"error": {"reason": ...}})."""
    erro = corpo.get("error")
    return corpo.get("reason") or (erro.get("reason") if isinstance(erro, dict) else None)


def _conferir(resposta: httpx.Response) -> None:
    """Traduz os erros do Spotify. 429 tem dois sabores desde jul/2026: limite de taxa
    (passageiro, com Retry-After) e cota esgotada ("reason": "QUOTA_EXCEEDED")."""
    if resposta.is_success:
        return
    if resposta.status_code == 429:
        espera = int(resposta.headers.get("retry-after", "30") or 30)
        if _razao(_json(resposta)) == "QUOTA_EXCEEDED":
            raise ErroSpotify(429, "a cota diária do app no Spotify acabou; volta amanhã", espera)
        raise ErroSpotify(429, "muitas chamadas ao Spotify; espere um pouco", espera)
    if resposta.status_code == 401:
        raise ErroSpotify(409, "o Spotify recusou o token: conecte de novo")
    if resposta.status_code == 403:
        raise ErroSpotify(403, "o Spotify negou a operação (a conta é Premium?)")
    raise ErroSpotify(502, f"o Spotify respondeu {resposta.status_code}")


def _contexto(usuario_id: int, campo: str) -> str:
    return f"spotify:{usuario_id}:{campo}"


# ---------------------------------------------------------------- conectar


SQL_GRAVAR_CONTA = text(
    """
    INSERT INTO spotify_contas (usuario_id, spotify_id, nome, escopos, refresh_token,
                                access_token, access_expira_em)
    VALUES (:usuario_id, :spotify_id, :nome, :escopos, :refresh, :access, :expira)
    ON CONFLICT (usuario_id) DO UPDATE
        SET spotify_id = EXCLUDED.spotify_id, nome = EXCLUDED.nome,
            escopos = EXCLUDED.escopos, refresh_token = EXCLUDED.refresh_token,
            access_token = EXCLUDED.access_token, access_expira_em = EXCLUDED.access_expira_em,
            conectado_em = now()
    """
)


def conectar(session: Session, cliente: ClienteSpotify, cifra: Cifra, *, usuario_id: int,
             code: str, verifier: str, redirect_uri: str) -> None:
    if not REDIRECT_PERMITIDO.fullmatch(redirect_uri):
        raise ErroSpotify(422, "redirect_uri precisa ser http://127.0.0.1:<porta>/callback")
    # Rede ANTES de abrir transação de escrita (as duas chamadas levam centenas de ms)
    tokens = cliente.trocar_codigo(code, verifier, redirect_uri)
    if not tokens.refresh_token:
        raise ErroSpotify(502, "o Spotify não devolveu o refresh token")
    perfil = cliente.perfil(tokens.access_token)
    session.execute(SQL_GRAVAR_CONTA, {
        "usuario_id": usuario_id, "spotify_id": perfil["id"], "nome": perfil.get("display_name"),
        "escopos": tokens.escopos,
        "refresh": cifra.cifrar(tokens.refresh_token, _contexto(usuario_id, "refresh")),
        "access": cifra.cifrar(tokens.access_token, _contexto(usuario_id, "access")),
        "expira": tokens.expira_em,
    })
    session.commit()


def desconectar(session: Session, *, usuario_id: int) -> None:
    session.execute(text("DELETE FROM spotify_contas WHERE usuario_id = :u"), {"u": usuario_id})
    session.commit()


# ---------------------------------------------------------------- token


def token_valido(session: Session, cliente: ClienteSpotify, cifra: Cifra, *, usuario_id: int,
                 forcar: bool = False, agora: datetime | None = None) -> tuple[str, datetime]:
    """Access token válido por pelo menos MARGEM, renovando se preciso.

    A linha da conta fica TRAVADA (SELECT ... FOR UPDATE) durante a renovação. Esta é uma
    exceção consciente à regra "rede fora de transação aberta": o Spotify pode trocar o
    refresh token a cada renovação. Se o Mac e o Windows renovassem ao mesmo tempo, os
    dois mandariam o MESMO refresh token antigo; um receberia o novo, e o outro gravaria
    por cima um token que pode já ter sido invalidado. Com o lock, o segundo espera, relê
    a linha, vê o access token novo e nem chama o Spotify. lock_timeout limita a espera.
    """
    agora = agora or datetime.now(UTC)
    session.execute(text("SET LOCAL lock_timeout = '15s'"))
    conta = session.execute(
        text("""SELECT refresh_token, access_token, access_expira_em
                FROM spotify_contas WHERE usuario_id = :u FOR UPDATE"""),
        {"u": usuario_id},
    ).one_or_none()
    if conta is None:
        session.rollback()
        raise NaoConectado()

    try:
        if not forcar and conta.access_token and conta.access_expira_em > agora + MARGEM:
            access = cifra.decifrar(conta.access_token, _contexto(usuario_id, "access"))
            session.commit()  # solta o lock
            return access, conta.access_expira_em
        refresh = cifra.decifrar(conta.refresh_token, _contexto(usuario_id, "refresh"))
    except ErroCifra as erro:
        session.rollback()
        raise ErroSpotify(500, "não consegui decifrar o token guardado (CIFRA_CHAVES mudou?)") from erro

    try:
        tokens = cliente.renovar(refresh)
    except ErroSpotify as erro:
        if erro.status == 409:  # autorização revogada: a conta guardada não serve mais
            session.execute(text("DELETE FROM spotify_contas WHERE usuario_id = :u"), {"u": usuario_id})
            session.commit()
        else:
            session.rollback()
        raise

    # Grava o novo access e, se veio, o novo refresh. Recifrar o refresh mesmo quando
    # não mudou leva-o para a chave ATUAL (rotação de chave acontece no uso).
    session.execute(
        text("""UPDATE spotify_contas
                SET access_token = :access, access_expira_em = :expira, refresh_token = :refresh
                WHERE usuario_id = :u"""),
        {
            "u": usuario_id,
            "access": cifra.cifrar(tokens.access_token, _contexto(usuario_id, "access")),
            "expira": tokens.expira_em,
            "refresh": cifra.cifrar(tokens.refresh_token or refresh, _contexto(usuario_id, "refresh")),
        },
    )
    session.commit()
    return tokens.access_token, tokens.expira_em


# ---------------------------------------------------------- estado e preferências


def estado(session: Session, *, usuario_id: int) -> dict:
    conta = session.execute(
        text("SELECT spotify_id, nome, conectado_em FROM spotify_contas WHERE usuario_id = :u"),
        {"u": usuario_id},
    ).one_or_none()
    no_intervalo = session.scalar(
        text("SELECT spotify_no_intervalo FROM preferencias_foco WHERE usuario_id = :u"), {"u": usuario_id}
    ) or "pausar"
    playlists = session.execute(
        text("""SELECT alvo, metodo, disciplina_id, uri, nome FROM spotify_playlists
                WHERE usuario_id = :u ORDER BY alvo, metodo NULLS FIRST, disciplina_id NULLS FIRST"""),
        {"u": usuario_id},
    ).mappings().all()
    return {
        "conectado": conta is not None,
        "nome": conta.nome if conta else None,
        "spotify_id": conta.spotify_id if conta else None,
        "conectado_em": conta.conectado_em if conta else None,
        "no_intervalo": no_intervalo,
        "playlists": [dict(p) for p in playlists],
    }


def salvar_preferencias(session: Session, *, usuario_id: int, no_intervalo: str,
                        playlists: list[dict]) -> None:
    """Troca TODA a configuração numa transação: UPSERT da preferência, DELETE das
    playlists antigas e INSERT das novas. Se uma linha violar uma regra (duas 'padrao',
    disciplina de outra pessoa), nada muda: a transação inteira é desfeita."""
    session.execute(
        text("""INSERT INTO preferencias_foco (usuario_id, spotify_no_intervalo)
                VALUES (:u, :p)
                ON CONFLICT (usuario_id) DO UPDATE SET spotify_no_intervalo = EXCLUDED.spotify_no_intervalo"""),
        {"u": usuario_id, "p": no_intervalo},
    )
    session.execute(text("DELETE FROM spotify_playlists WHERE usuario_id = :u"), {"u": usuario_id})
    if playlists:
        session.execute(
            text("""INSERT INTO spotify_playlists (usuario_id, alvo, metodo, disciplina_id, uri, nome)
                    VALUES (:usuario_id, :alvo, :metodo, :disciplina_id, :uri, :nome)"""),
            [{"usuario_id": usuario_id, **p} for p in playlists],
        )
    session.commit()
