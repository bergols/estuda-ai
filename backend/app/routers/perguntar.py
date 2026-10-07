from fastapi import APIRouter, HTTPException, status

from app.deps import DisciplinaDoUsuario, EmbedderDep, LLMDep, SessionDep
from app.schemas import Citacao, GeracaoResumo, PerguntaEntrada, RespostaPergunta
from app.servicos import rag
from app.servicos.llm import ErroGeracao

router = APIRouter(tags=["geracao"])


def erro_http(erro: ErroGeracao) -> HTTPException:
    """Falha do LLM vira 502 (o "servidor de cima" falhou), com o id da auditoria."""
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        {
            "mensagem": "falha ao gerar com o LLM",
            "status": erro.status,
            "detalhe": erro.mensagem,
            "geracao_id": erro.geracao.id if erro.geracao else None,
        },
    )


@router.post("/disciplinas/{disciplina_id}/perguntar", response_model=RespostaPergunta)
def perguntar(
    dados: PerguntaEntrada,
    disciplina: DisciplinaDoUsuario,
    session: SessionDep,
    embedder: EmbedderDep,
    llm: LLMDep,
):
    """Pergunta ao material da disciplina (RAG): resposta + citações (trecho, material, página).

    Se os trechos recuperados não cobrem a pergunta, encontrado = false.
    """
    usuario_id, disciplina_id = disciplina.usuario_id, disciplina.id
    try:
        r = rag.perguntar(
            session, usuario_id=usuario_id, disciplina_id=disciplina_id,
            pergunta=dados.pergunta, k=dados.k, embedder=embedder, llm=llm,
        )
    except ErroGeracao as erro:
        raise erro_http(erro) from erro
    return RespostaPergunta(
        resposta=r.resposta,
        encontrado=r.encontrado,
        citacoes=[
            Citacao(
                trecho_id=t.trecho_id, material_id=t.material_id,
                material_titulo=t.material_titulo, pagina=t.pagina, pagina_fim=t.pagina_fim,
                trecho=t.conteudo[:400],
            )
            for t in r.citacoes
        ],
        geracao=GeracaoResumo.model_validate(r.geracao) if r.geracao else None,
    )
