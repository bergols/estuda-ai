from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.deps import SessionDep, UsuarioAtual
from app.schemas import TokenSaida, UsuarioLer
from app.servicos import auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenSaida)
def login(dados: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep):
    """Troca e-mail (no campo `username`) e senha por um JWT válido por 30 dias.

    Formulário (application/x-www-form-urlencoded), o formato do fluxo "password" do
    OAuth2, para o botão Authorize do /docs funcionar.
    """
    try:
        usuario = auth.autenticar(session, dados.username, dados.password)
    except auth.ErroAuth as erro:
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
