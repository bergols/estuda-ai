from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from app.db import constraint_violada
from app.deps import CifraDep, SessionDep, SpotifyDep, UsuarioAtual
from app.schemas import (
    PlaylistDoSpotify,
    SpotifyConectar,
    SpotifyConfig,
    SpotifyEstado,
    SpotifyPreferencias,
    SpotifyToken,
    SpotifyTokenEntrada,
)
from app.servicos import spotify
from app.servicos.spotify import ErroSpotify, NaoConectado

router = APIRouter(prefix="/spotify", tags=["spotify"])

NAO_CONECTADO = HTTPException(status.HTTP_404_NOT_FOUND, "o Spotify não está conectado")


def _http(erro: ErroSpotify) -> HTTPException:
    cabecalhos = {"Retry-After": str(erro.retry_after)} if erro.retry_after else None
    return HTTPException(erro.status, erro.mensagem, headers=cabecalhos)


@router.get("/config", response_model=SpotifyConfig)
def config(usuario: UsuarioAtual, cliente: SpotifyDep):
    """O que o app desktop precisa para montar a URL de autorização (público no PKCE)."""
    return SpotifyConfig(client_id=cliente.client_id, escopos=spotify.ESCOPOS)


@router.get("", response_model=SpotifyEstado)
def estado(usuario: UsuarioAtual, session: SessionDep):
    """Conectado ou não, com as playlists escolhidas e a preferência do intervalo.
    Nunca devolve token."""
    return spotify.estado(session, usuario_id=usuario.id)


@router.post("/conectar", response_model=SpotifyEstado)
def conectar(dados: SpotifyConectar, usuario: UsuarioAtual, session: SessionDep,
             cliente: SpotifyDep, cifra: CifraDep):
    """Troca o código da autorização (PKCE) pelos tokens e guarda o refresh cifrado."""
    try:
        spotify.conectar(session, cliente, cifra, usuario_id=usuario.id, code=dados.code,
                         verifier=dados.code_verifier, redirect_uri=dados.redirect_uri)
    except ErroSpotify as erro:
        raise _http(erro) from erro
    return spotify.estado(session, usuario_id=usuario.id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def desconectar(usuario: UsuarioAtual, session: SessionDep):
    """Apaga a conexão guardada (as playlists escolhidas continuam). Para revogar também
    no Spotify: spotify.com/account/apps."""
    spotify.desconectar(session, usuario_id=usuario.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/token", response_model=SpotifyToken)
def token(dados: SpotifyTokenEntrada, usuario: UsuarioAtual, session: SessionDep,
          cliente: SpotifyDep, cifra: CifraDep):
    """Access token válido por pelo menos 2 minutos (renova se preciso). POST: pode
    mudar estado (renovação) e devolve um segredo; não deve ficar em cache."""
    try:
        access, expira_em = spotify.token_valido(session, cliente, cifra, usuario_id=usuario.id,
                                                 forcar=dados.forcar)
    except NaoConectado as erro:
        raise NAO_CONECTADO from erro
    except ErroSpotify as erro:
        raise _http(erro) from erro
    return SpotifyToken(access_token=access, expira_em=expira_em)


@router.get("/minhas-playlists", response_model=list[PlaylistDoSpotify])
def minhas_playlists(usuario: UsuarioAtual, session: SessionDep, cliente: SpotifyDep, cifra: CifraDep):
    """As playlists da conta (para escolher na configuração)."""
    try:
        access, _ = spotify.token_valido(session, cliente, cifra, usuario_id=usuario.id)
        return cliente.minhas_playlists(access)
    except NaoConectado as erro:
        raise NAO_CONECTADO from erro
    except ErroSpotify as erro:
        raise _http(erro) from erro


@router.put("/preferencias", response_model=SpotifyEstado)
def preferencias(dados: SpotifyPreferencias, usuario: UsuarioAtual, session: SessionDep):
    """Troca toda a configuração de música (numa transação: ou tudo, ou nada)."""
    try:
        spotify.salvar_preferencias(session, usuario_id=usuario.id, no_intervalo=dados.no_intervalo,
                                    playlists=[p.model_dump() for p in dados.playlists])
    except IntegrityError as erro:
        session.rollback()
        nome = constraint_violada(erro) or ""
        if nome == "fk_spotify_playlists_disciplina_id_disciplinas":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "disciplina não encontrada") from erro
        if nome.startswith("uq_spotify_playlists"):
            raise HTTPException(422, "mais de uma playlist para o mesmo alvo") from erro
        raise HTTPException(422, f"configuração inválida ({nome})") from erro
    return spotify.estado(session, usuario_id=usuario.id)
