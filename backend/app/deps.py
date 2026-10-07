from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Usuario

SessionDep = Annotated[Session, Depends(get_session)]


def usuario_atual(
    session: SessionDep,
    x_usuario_id: Annotated[int, Header(description="Provisório até a autenticação")],
) -> Usuario:
    """Identifica o usuário pelo header X-Usuario-Id.

    PROVISÓRIO: não há autenticação ainda. Quando houver, só esta função muda
    (passa a validar um token) e as rotas continuam iguais.
    """
    usuario = session.get(Usuario, x_usuario_id)
    if usuario is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário não identificado")
    return usuario


UsuarioAtual = Annotated[Usuario, Depends(usuario_atual)]
