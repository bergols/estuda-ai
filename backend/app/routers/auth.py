from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.deps import IpDoCliente, SessionDep, SettingsDep, UsuarioAtual, http_429
from app.schemas import TokenSaida, UsuarioLer
from app.servicos import auth, limites

JANELA_LOGIN = timedelta(minutes=15)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenSaida)
def login(
    ip: IpDoCliente,
    dados: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    settings: SettingsDep,
):
    """Troca e-mail (no campo `username`) e senha por um JWT válido por 30 dias.

    Formulário (application/x-www-form-urlencoded), o formato do fluxo "password" do
    OAuth2, para o botão Authorize do /docs funcionar.

    Contra força bruta: no máximo LIMITE_LOGIN_POR_IP tentativas por IP e
    LIMITE_LOGIN_POR_EMAIL falhas por e-mail a cada 15 minutos (429 depois disso).
    """
    chave_email = f"login:email:{dados.username.strip().lower()}"
    try:
        # Por IP: conta toda tentativa. Por e-mail: só confere aqui e conta as FALHAS
        # (logar certo várias vezes não tranca a sua própria conta). A conferência vem
        # ANTES da senha: depois do limite, nem a senha certa entra até a janela virar.
        limites.consumir(session, f"login:ip:{ip}", settings.limite_login_por_ip, JANELA_LOGIN)
        limites.verificar(session, chave_email, settings.limite_login_por_email, JANELA_LOGIN)
    except limites.Excedido as erro:
        raise http_429(erro) from erro
    try:
        usuario = auth.autenticar(session, dados.username, dados.password)
    except auth.ErroAuth as erro:
        try:  # registra a falha; quem barra é o verificar() lá em cima
            limites.consumir(session, chave_email, settings.limite_login_por_email, JANELA_LOGIN)
        except limites.Excedido:
            pass
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(erro), headers={"WWW-Authenticate": "Bearer"}
        ) from erro
    token = auth.emitir_token(usuario)
    return TokenSaida(access_token=token.valor, expira_em=token.expira_em)


@router.get("/eu", response_model=UsuarioLer)
def eu(usuario: UsuarioAtual):
    return usuario


@router.post("/sair-de-todos", status_code=status.HTTP_204_NO_CONTENT)
def sair_de_todos(usuario: UsuarioAtual, session: SessionDep):
    """Invalida todos os tokens deste usuário, em todos os dispositivos (inclusive este)."""
    auth.revogar_tokens(session, usuario.id)
