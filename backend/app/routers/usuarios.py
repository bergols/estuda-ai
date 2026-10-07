from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.db import constraint_violada
from app.deps import SessionDep
from app.models import Usuario
from app.schemas import UsuarioCriar, UsuarioLer

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


@router.post("", response_model=UsuarioLer, status_code=status.HTTP_201_CREATED)
def criar_usuario(dados: UsuarioCriar, session: SessionDep):
    """Cadastro mínimo, só para existir um dono das disciplinas até haver autenticação."""
    usuario = Usuario(nome=dados.nome, email=dados.email)
    session.add(usuario)
    try:
        session.commit()
    except IntegrityError as erro:
        session.rollback()
        if constraint_violada(erro) == "uq_usuarios_email_lower":
            raise HTTPException(status.HTTP_409_CONFLICT, "e-mail já cadastrado") from erro
        raise
    return usuario
