from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import DisciplinaDoUsuario, EmbedderDep, LLMDep, SessionDep
from app.models import Flashcard
from app.routers.perguntar import erro_http
from app.schemas import FlashcardLer, FlashcardsGeradosSaida, GeracaoResumo, GerarEntrada
from app.servicos import gerar
from app.servicos.llm import ErroGeracao

router = APIRouter(prefix="/disciplinas/{disciplina_id}/flashcards", tags=["flashcards"])


@router.post("/gerar", response_model=FlashcardsGeradosSaida, status_code=201)
def gerar_flashcards(
    dados: GerarEntrada,
    disciplina: DisciplinaDoUsuario,
    session: SessionDep,
    embedder: EmbedderDep,
    llm: LLMDep,
):
    """Gera flashcards de um material (até 30 trechos distribuídos) ou de um tema.

    Cards muito parecidos com os já existentes na disciplina são descartados
    (similaridade de cosseno >= limiar) e listados em `descartados`.
    """
    usuario_id, disciplina_id = disciplina.usuario_id, disciplina.id
    try:
        r = gerar.gerar_flashcards(
            session, usuario_id=usuario_id, disciplina_id=disciplina_id,
            material_id=dados.material_id, tema=dados.tema, quantidade=dados.quantidade,
            embedder=embedder, llm=llm,
        )
    except gerar.ErroContexto as erro:
        raise HTTPException(erro.status, erro.mensagem) from erro
    except ErroGeracao as erro:
        raise erro_http(erro) from erro
    return FlashcardsGeradosSaida(
        criados=[FlashcardLer.model_validate(c) for c in r.criados],
        descartados=[vars(d) for d in r.descartados],
        limiar_duplicata=gerar.LIMIAR_DUPLICATA,
        geracao=GeracaoResumo.model_validate(r.geracao),
    )


@router.get("", response_model=list[FlashcardLer])
def listar_flashcards(disciplina: DisciplinaDoUsuario, session: SessionDep):
    # selectinload: os trechos de TODOS os cards vêm numa 2ª consulta só
    # (WHERE flashcard_id IN (...)), em vez de uma consulta por card (o
    # problema "N+1 consultas" de quem usa ORM sem perceber).
    consulta = (
        select(Flashcard)
        .where(Flashcard.disciplina_id == disciplina.id)
        .options(selectinload(Flashcard.trechos))
        .order_by(Flashcard.criado_em.desc(), Flashcard.id.desc())
    )
    return session.scalars(consulta).all()
