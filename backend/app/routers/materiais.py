import hashlib
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import constraint_violada
from app.deps import (
    DisciplinaDoUsuario,
    EmbedderDep,
    FabricaSessaoDep,
    SessionDep,
    SettingsDep,
)
from app.models import Material
from app.schemas import MaterialLer
from app.servicos.processamento import processar_material

router = APIRouter(prefix="/disciplinas/{disciplina_id}/materiais", tags=["materiais"])

TIPOS_ACEITOS = {"application/pdf"}
ASSINATURA_PDF = b"%PDF-"
BLOCO = 1024 * 1024


def _material_da_disciplina(session, disciplina, material_id: int) -> Material:
    material = session.scalar(
        select(Material).where(
            Material.id == material_id, Material.disciplina_id == disciplina.id
        )
    )
    if material is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "material não encontrado")
    return material


def _salvar_arquivo(arquivo: UploadFile, destino: Path, limite: int) -> tuple[str, int]:
    """Grava o upload em disco em blocos, calculando o SHA-256 no caminho.

    Lê em blocos de 1 MB para nunca ter o arquivo inteiro na memória, e para
    no primeiro byte acima do limite. Grava num nome temporário e só renomeia
    no fim (rename é atômico): nunca existe um .pdf pela metade no volume.
    """
    temporario = destino.with_suffix(".parcial")
    sha = hashlib.sha256()
    tamanho = 0
    try:
        with temporario.open("wb") as saida:
            while bloco := arquivo.file.read(BLOCO):
                # O content-type vem do cliente e pode mentir; a assinatura
                # ("magic bytes") no início do arquivo é o que vale.
                if tamanho == 0 and not bloco.startswith(ASSINATURA_PDF):
                    raise HTTPException(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "o arquivo não é um PDF válido"
                    )
                tamanho += len(bloco)
                if tamanho > limite:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        f"o arquivo passa do limite de {limite // (1024 * 1024)} MB",
                    )
                sha.update(bloco)
                saida.write(bloco)
        if tamanho == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "arquivo vazio")
        temporario.rename(destino)
    finally:
        temporario.unlink(missing_ok=True)
    return sha.hexdigest(), tamanho


@router.post("", response_model=MaterialLer, status_code=status.HTTP_202_ACCEPTED)
def enviar_material(
    disciplina: DisciplinaDoUsuario,
    arquivo: UploadFile,
    tarefas: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    fabrica: FabricaSessaoDep,
    embedder: EmbedderDep,
    titulo: Annotated[str | None, Form(max_length=200)] = None,
):
    """Recebe um PDF e agenda o processamento. Responde 202 (aceito, ainda não
    processado): acompanhe o campo status em GET .../materiais/{id}."""
    if arquivo.content_type not in TIPOS_ACEITOS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "envie um arquivo PDF")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    # Nome gerado pelo servidor: o nome enviado pelo cliente poderia conter
    # "../" ou colidir com outro arquivo. No banco guardamos o caminho RELATIVO
    # à pasta de uploads, para poder mudar o ponto de montagem sem migrar dados.
    nome_no_disco = f"{uuid4().hex}.pdf"
    destino = settings.upload_dir / nome_no_disco
    sha256, tamanho = _salvar_arquivo(arquivo, destino, settings.max_upload_mb * 1024 * 1024)

    titulo = (titulo or "").strip() or Path(arquivo.filename or "").stem or "Material sem título"
    material = Material(
        disciplina_id=disciplina.id,
        titulo=titulo,
        tipo="pdf",
        nome_arquivo=arquivo.filename,
        hash_sha256=sha256,
        tamanho_bytes=tamanho,
        caminho_arquivo=nome_no_disco,
    )
    session.add(material)
    try:
        session.commit()
    except IntegrityError as erro:
        session.rollback()
        # Disco e banco são dois sistemas: não há uma transação que cubra os dois.
        # Se o banco recusou, desfazemos o arquivo à mão.
        destino.unlink(missing_ok=True)
        if constraint_violada(erro) == "uq_materiais_disciplina_id_hash_sha256":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "este arquivo já foi enviado para esta disciplina"
            ) from erro
        raise

    # Roda depois que a resposta é enviada, no mesmo processo.
    tarefas.add_task(processar_material, material.id, settings.upload_dir, fabrica, embedder)
    return material


@router.get("", response_model=list[MaterialLer])
def listar_materiais(disciplina: DisciplinaDoUsuario, session: SessionDep):
    # Coberto pelo índice único (disciplina_id, hash_sha256): prefixo disciplina_id.
    consulta = (
        select(Material)
        .where(Material.disciplina_id == disciplina.id)
        .order_by(Material.criado_em.desc(), Material.id.desc())
    )
    return session.scalars(consulta).all()


@router.get("/{material_id}", response_model=MaterialLer)
def obter_material(material_id: int, disciplina: DisciplinaDoUsuario, session: SessionDep):
    return _material_da_disciplina(session, disciplina, material_id)


@router.post(
    "/{material_id}/reprocessar", response_model=MaterialLer, status_code=status.HTTP_202_ACCEPTED
)
def reprocessar_material(
    material_id: int,
    disciplina: DisciplinaDoUsuario,
    tarefas: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    fabrica: FabricaSessaoDep,
    embedder: EmbedderDep,
):
    """Devolve um material com erro para a fila (erro -> pendente)."""
    material = _material_da_disciplina(session, disciplina, material_id)
    # Transição de estado condicional: só sai de 'erro'. Se dois pedidos chegarem
    # juntos, o segundo encontra 'pendente' e atualiza 0 linhas.
    voltou = session.execute(
        update(Material)
        .where(Material.id == material.id, Material.status == "erro")
        .values(status="pendente", erro_mensagem=None)
        .returning(Material.id)
    ).scalar_one_or_none()
    session.commit()
    if voltou is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "só materiais com erro podem ser reprocessados")
    session.refresh(material)
    tarefas.add_task(processar_material, material.id, settings.upload_dir, fabrica, embedder)
    return material


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def apagar_material(
    material_id: int, disciplina: DisciplinaDoUsuario, session: SessionDep, settings: SettingsDep
):
    material = _material_da_disciplina(session, disciplina, material_id)
    caminho = material.caminho_arquivo
    session.delete(material)  # ON DELETE CASCADE apaga os trechos
    session.commit()
    # Arquivo só depois do COMMIT: se o banco falhar, o arquivo continua lá e
    # nada se perde. O contrário (apagar antes) deixaria um registro sem arquivo.
    if caminho:
        (settings.upload_dir / caminho).unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
