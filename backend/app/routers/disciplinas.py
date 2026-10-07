from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import constraint_violada
from app.deps import SessionDep, UsuarioAtual
from app.models import Disciplina, Usuario
from app.schemas import DisciplinaAtualizar, DisciplinaCriar, DisciplinaLer

router = APIRouter(prefix="/disciplinas", tags=["disciplinas"])


def _buscar(session: Session, usuario: Usuario, disciplina_id: int) -> Disciplina:
    # O filtro por usuario_id faz parte da consulta: disciplina de outro usuário
    # responde 404, como se não existisse (não revela que o id existe).
    disciplina = session.scalar(
        select(Disciplina).where(
            Disciplina.id == disciplina_id, Disciplina.usuario_id == usuario.id
        )
    )
    if disciplina is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "disciplina não encontrada")
    return disciplina


def _commit(session: Session) -> None:
    # Não fazemos "SELECT para ver se o nome já existe" antes do INSERT: entre o
    # SELECT e o INSERT outra requisição poderia inserir o mesmo nome (condição de
    # corrida). Quem garante a unicidade é o índice UNIQUE; aqui só traduzimos o erro.
    try:
        session.commit()
    except IntegrityError as erro:
        session.rollback()
        if constraint_violada(erro) == "uq_disciplinas_usuario_nome":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "já existe uma disciplina com esse nome"
            ) from erro
        raise


@router.post("", response_model=DisciplinaLer, status_code=status.HTTP_201_CREATED)
def criar_disciplina(dados: DisciplinaCriar, session: SessionDep, usuario: UsuarioAtual):
    disciplina = Disciplina(usuario_id=usuario.id, **dados.model_dump())
    session.add(disciplina)
    _commit(session)
    return disciplina


@router.get("", response_model=list[DisciplinaLer])
def listar_disciplinas(
    session: SessionDep,
    usuario: UsuarioAtual,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    # Ordenar por lower(nome) dá ordem alfabética sem diferenciar maiúsculas.
    # O id desempata nomes iguais, para a paginação ser estável.
    consulta = (
        select(Disciplina)
        .where(Disciplina.usuario_id == usuario.id)
        .order_by(func.lower(Disciplina.nome), Disciplina.id)
        .limit(limit)
        .offset(offset)
    )
    return session.scalars(consulta).all()


@router.get("/{disciplina_id}", response_model=DisciplinaLer)
def obter_disciplina(disciplina_id: int, session: SessionDep, usuario: UsuarioAtual):
    return _buscar(session, usuario, disciplina_id)


@router.patch("/{disciplina_id}", response_model=DisciplinaLer)
def atualizar_disciplina(
    disciplina_id: int, dados: DisciplinaAtualizar, session: SessionDep, usuario: UsuarioAtual
):
    disciplina = _buscar(session, usuario, disciplina_id)
    for campo, valor in dados.model_dump(exclude_unset=True).items():
        setattr(disciplina, campo, valor)
    _commit(session)
    return disciplina


@router.delete("/{disciplina_id}", status_code=status.HTTP_204_NO_CONTENT)
def apagar_disciplina(disciplina_id: int, session: SessionDep, usuario: UsuarioAtual):
    # Um único DELETE: o ON DELETE CASCADE do banco apaga materiais, trechos,
    # flashcards, questões etc. (passive_deletes=True evita o ORM carregá-los).
    disciplina = _buscar(session, usuario, disciplina_id)
    session.delete(disciplina)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
