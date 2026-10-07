from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.deps import SessionDep, UsuarioAtual
from app.models import Disciplina
from app.schemas import EstadoSM2Saida, FilaDoDia, ResultadoRevisaoSaida, RevisaoEntrada
from app.servicos import revisao

router = APIRouter(prefix="/revisoes", tags=["revisoes"])


@router.get("/hoje", response_model=FilaDoDia)
def fila_de_hoje(
    usuario: UsuarioAtual,
    session: SessionDep,
    disciplina_id: int | None = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 20,
):
    """Cards que vencem até o fim de hoje (no fuso do usuário), do mais atrasado
    para o menos atrasado."""
    if disciplina_id is not None:
        dona = session.scalar(
            select(Disciplina.id).where(
                Disciplina.id == disciplina_id, Disciplina.usuario_id == usuario.id
            )
        )
        if dona is None:
            raise HTTPException(404, "disciplina não encontrada")
    fila = revisao.fila_do_dia(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, limite=limite
    )
    return FilaDoDia(fuso_horario=fila.fuso_horario, fim_de_hoje=fila.fim_de_hoje, cards=fila.cards)


@router.post("/{flashcard_id}", response_model=ResultadoRevisaoSaida)
def revisar(flashcard_id: int, dados: RevisaoEntrada, usuario: UsuarioAtual, session: SessionDep):
    """Registra a nota (0 a 5): atualiza o estado do SM-2 e grava o histórico.

    `versao` é a que veio na fila. Se o card já foi revisado por outra requisição
    (duplo clique, outra aba), responde 409 com a versão atual e nada é gravado.
    """
    usuario_id = usuario.id
    try:
        r = revisao.registrar_revisao(
            session, usuario_id=usuario_id, flashcard_id=flashcard_id,
            nota=dados.nota, versao=dados.versao,
        )
    except revisao.ErroRevisao as erro:
        detalhe = {"mensagem": erro.mensagem}
        if erro.versao_atual is not None:
            detalhe["versao_atual"] = erro.versao_atual
        raise HTTPException(erro.status, detalhe) from erro
    return ResultadoRevisaoSaida(
        flashcard_id=r.flashcard_id,
        nota=r.nota,
        anterior=EstadoSM2Saida(
            facilidade=r.anterior.facilidade, intervalo_dias=r.anterior.intervalo,
            repeticoes=r.anterior.repeticoes,
        ),
        novo=EstadoSM2Saida(
            facilidade=r.novo.facilidade, intervalo_dias=r.novo.intervalo,
            repeticoes=r.novo.repeticoes,
        ),
        proxima_revisao=r.proxima_revisao,
        versao=r.versao,
        historico_id=r.historico_id,
    )
