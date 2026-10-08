import hmac
import ipaddress
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, get_session
from app.models import Disciplina, Usuario
from app.servicos import auth, limites
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


# tokenUrl faz o botão "Authorize" do /docs (Swagger) funcionar com e-mail e senha.
_bearer = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def usuario_atual(session: SessionDep, token: Annotated[str | None, Depends(_bearer)]) -> Usuario:
    """O usuário do token "Authorization: Bearer <jwt>". Toda rota que mexe com dados
    depende desta função, e toda consulta filtra pelo usuário que ela devolve."""
    nao_autorizado = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "não autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise nao_autorizado
    try:
        usuario_id, versao = auth.ler_token(token)
    except auth.ErroAuth as erro:
        raise nao_autorizado from erro
    usuario = session.get(Usuario, usuario_id)
    # versão diferente = token revogado ("sair de todos" ou troca de senha)
    if usuario is None or usuario.versao_token != versao:
        raise nao_autorizado
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


def ip_do_cliente(request: Request, settings: SettingsDep) -> str:
    """IP usado no rate limit de login.

    Headers como X-Forwarded-For são escritos por quem faz a requisição: confiar neles
    sem conferir a origem deixa qualquer um "trocar de IP" a cada tentativa. Aqui só o
    BFF (o servidor do Next.js) é confiável, e ele prova quem é com X-BFF-Segredo.
    hmac.compare_digest compara em tempo constante (não vaza, pelo tempo, quantos
    caracteres do segredo acertaram).
    """
    direto = request.client.host if request.client else "desconhecido"
    if settings.bff_segredo is None:
        return direto
    enviado = request.headers.get("x-bff-segredo", "")
    if not hmac.compare_digest(enviado.encode(), settings.bff_segredo.get_secret_value().encode()):
        return direto
    try:
        return str(ipaddress.ip_address(request.headers.get("x-cliente-ip", "").strip()))
    except ValueError:
        return direto


IpDoCliente = Annotated[str, Depends(ip_do_cliente)]


def http_429(erro: limites.Excedido) -> HTTPException:
    # Retry-After diz ao cliente quantos segundos esperar (padrão HTTP).
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        erro.mensagem,
        headers={"Retry-After": str(erro.tentar_de_novo_em_s)},
    )


def protecao_ia(usuario: UsuarioAtual, session: SessionDep, settings: SettingsDep) -> None:
    """Antes de qualquer chamada ao LLM: rate limit por usuário (rajadas) e cota
    diária (teto de custo). Se alguém achar o link e tiver um token, o estrago
    máximo por dia fica limitado a LIMITE_GERACOES_DIA gerações."""
    try:
        limites.consumir(
            session, f"ia:usuario:{usuario.id}", settings.limite_ia_por_minuto, timedelta(minutes=1)
        )
        limites.verificar_cota_diaria(session, usuario.id, settings.limite_geracoes_dia)
    except limites.Excedido as erro:
        raise http_429(erro) from erro


ProtecaoIA = Annotated[None, Depends(protecao_ia)]
