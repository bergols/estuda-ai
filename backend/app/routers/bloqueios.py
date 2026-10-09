from fastapi import APIRouter

from app.deps import SessionDep, UsuarioAtual
from app.schemas import Bloqueios
from app.servicos import bloqueios

router = APIRouter(prefix="/bloqueios", tags=["bloqueios"])


@router.get("", response_model=Bloqueios)
def ler(usuario: UsuarioAtual, session: SessionDep):
    """Sites e programas que o modo foco bloqueia, e as preferências do bloqueio."""
    return bloqueios.ler(session, usuario_id=usuario.id)


@router.put("", response_model=Bloqueios)
def salvar(dados: Bloqueios, usuario: UsuarioAtual, session: SessionDep):
    """Troca toda a configuração de bloqueio (numa transação: ou tudo, ou nada).

    Domínios chegam como se cola do navegador ("https://www.youtube.com/watch") e são
    guardados como domínio ("youtube.com"); repetidos contam uma vez.
    """
    bloqueios.salvar(session, usuario_id=usuario.id, dados=dados)
    return bloqueios.ler(session, usuario_id=usuario.id)
