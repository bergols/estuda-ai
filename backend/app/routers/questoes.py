from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.deps import DisciplinaDoUsuario, EmbedderDep, LLMDep, ProtecaoIA, SessionDep
from app.models import Questao
from app.routers.perguntar import erro_http
from app.schemas import (
    GeracaoResumo,
    GerarEntrada,
    QuestaoLer,
    QuestoesGeradasSaida,
    TentativaEntrada,
    TentativaResultado,
)
from app.servicos import gerar
from app.servicos.llm import ErroGeracao

router = APIRouter(prefix="/disciplinas/{disciplina_id}/questoes", tags=["questoes"])


def _com_relacionamentos():
    return selectinload(Questao.alternativas), selectinload(Questao.trechos)


@router.post("/gerar", response_model=QuestoesGeradasSaida, status_code=201)
def gerar_questoes(
    dados: GerarEntrada,
    disciplina: DisciplinaDoUsuario,
    _: ProtecaoIA,  # depois da disciplina: dado alheio continua 404
    session: SessionDep,
    embedder: EmbedderDep,
    llm: LLMDep,
):
    """Gera questões de múltipla escolha (4 alternativas, 1 correta, explicação).

    A resposta não traz o gabarito: ele aparece ao registrar uma tentativa.
    """
    usuario_id, disciplina_id = disciplina.usuario_id, disciplina.id
    try:
        r = gerar.gerar_questoes(
            session, usuario_id=usuario_id, disciplina_id=disciplina_id,
            material_id=dados.material_id, tema=dados.tema,
            quantidade=min(dados.quantidade, 10), embedder=embedder, llm=llm,
        )
    except gerar.ErroContexto as erro:
        raise HTTPException(erro.status, erro.mensagem) from erro
    except ErroGeracao as erro:
        raise erro_http(erro) from erro
    ids = [q.id for q in r.questoes]
    questoes = session.scalars(
        select(Questao).where(Questao.id.in_(ids)).options(*_com_relacionamentos()).order_by(Questao.id)
    ).all()
    return QuestoesGeradasSaida(
        questoes=[QuestaoLer.model_validate(q) for q in questoes],
        geracao=GeracaoResumo.model_validate(r.geracao),
    )


@router.get("", response_model=list[QuestaoLer])
def listar_questoes(disciplina: DisciplinaDoUsuario, session: SessionDep):
    consulta = (
        select(Questao)
        .where(Questao.disciplina_id == disciplina.id)
        .options(*_com_relacionamentos())
        .order_by(Questao.criado_em.desc(), Questao.id.desc())
    )
    return session.scalars(consulta).all()


# INSERT ... SELECT: a linha da tentativa é montada a partir da alternativa
# escolhida. "correta" vem de alternativas.correta (o cliente só diz a letra) e o
# JOIN com questoes garante que a questão é desta disciplina. RETURNING devolve o
# resultado na mesma ida ao banco. Se a letra não existe, o SELECT não acha nada
# e nada é inserido (0 linhas).
SQL_REGISTRAR_TENTATIVA = text("""
    INSERT INTO tentativas (questao_id, alternativa_id, correta, tempo_ms)
    SELECT a.questao_id, a.id, a.correta, :tempo_ms
    FROM alternativas AS a
    JOIN questoes AS q ON q.id = a.questao_id
    WHERE q.id = :questao_id AND q.disciplina_id = :disciplina_id AND a.letra = :letra
    RETURNING id, correta, tempo_ms
""")


@router.post("/{questao_id}/tentativas", response_model=TentativaResultado, status_code=201)
def responder_questao(
    questao_id: int,
    dados: TentativaEntrada,
    disciplina: DisciplinaDoUsuario,
    session: SessionDep,
):
    """Registra a resposta (alternativa escolhida, acerto, tempo) e devolve o gabarito."""
    tentativa = session.execute(
        SQL_REGISTRAR_TENTATIVA,
        {
            "questao_id": questao_id,
            "disciplina_id": disciplina.id,
            "letra": dados.alternativa,
            "tempo_ms": dados.tempo_ms,
        },
    ).one_or_none()
    if tentativa is None:
        existe = session.scalar(
            select(Questao.id).where(Questao.id == questao_id, Questao.disciplina_id == disciplina.id)
        )
        if existe is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "questão não encontrada")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"a questão não tem a alternativa {dados.alternativa}",
        )
    gabarito = session.execute(
        text("""
            SELECT a.letra, q.explicacao
            FROM questoes AS q JOIN alternativas AS a ON a.questao_id = q.id AND a.correta
            WHERE q.id = :questao_id
        """),
        {"questao_id": questao_id},
    ).one()
    session.commit()
    return TentativaResultado(
        tentativa_id=tentativa.id,
        correta=tentativa.correta,
        alternativa_escolhida=dados.alternativa,
        alternativa_correta=gabarito.letra,
        explicacao=gabarito.explicacao,
        tempo_ms=tentativa.tempo_ms,
    )
