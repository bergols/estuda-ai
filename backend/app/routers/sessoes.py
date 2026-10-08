from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import SessionDep, UsuarioAtual
from app.schemas import LoteSessoes, ResultadoSincronizacao, SessaoLer
from app.servicos import sessoes

router = APIRouter(prefix="/sessoes", tags=["sessoes"])


@router.post("/sincronizar", response_model=ResultadoSincronizacao)
def sincronizar(lote: LoteSessoes, usuario: UsuarioAtual, session: SessionDep):
    """Recebe um lote de sessões de estudo do app desktop (com pausas e eventos).

    Idempotente: reenviar o mesmo lote não duplica nada. Cada sessão volta com o seu
    resultado (criada, atualizada, sem_mudanca ou recusada); uma sessão recusada não
    impede as outras de serem gravadas.
    """
    return ResultadoSincronizacao(
        sessoes=sessoes.sincronizar(session, usuario_id=usuario.id, lote=lote)
    )


@router.get("", response_model=list[SessaoLer])
def listar(
    usuario: UsuarioAtual,
    session: SessionDep,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Sessões mais recentes, com foco efetivo, pausas e interrupções."""
    return sessoes.listar(session, usuario_id=usuario.id, limite=limite)
