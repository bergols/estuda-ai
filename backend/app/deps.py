from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, get_session
from app.models import Disciplina, Usuario
from app.servicos.embeddings import Embedder, get_embedder
from app.servicos.llm import ClienteLLM
from app.servicos.processamento import FabricaSessao

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


@lru_cache
def _cliente_llm(modelo: str) -> ClienteLLM:
    return ClienteLLM(modelo)


def get_llm(settings: Annotated[Settings, Depends(get_settings)]) -> ClienteLLM:
    """Um cliente por modelo e por processo (reaproveita conexões HTTP).
    Nos testes é substituído por um ClienteLLM com AnthropicFalso."""
    return _cliente_llm(settings.anthropic_model)


LLMDep = Annotated[ClienteLLM, Depends(get_llm)]


def get_fabrica_sessao() -> FabricaSessao:
    """Tarefas em background não podem usar a sessão da requisição (ela é
    fechada quando a resposta sai); recebem a fábrica e abrem as próprias."""
    return SessionLocal


FabricaSessaoDep = Annotated[FabricaSessao, Depends(get_fabrica_sessao)]


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


def disciplina_do_usuario(
    disciplina_id: int, session: SessionDep, usuario: UsuarioAtual
) -> Disciplina:
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


DisciplinaDoUsuario = Annotated[Disciplina, Depends(disciplina_do_usuario)]
