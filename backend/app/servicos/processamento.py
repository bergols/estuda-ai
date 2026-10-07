"""Processamento de um material em background: PDF -> trechos -> embeddings -> banco.

Máquina de estados de materiais.status:

    pendente --(reserva)--> processando --(sucesso)--> concluido
                                        \\--(falha)---> erro

São três transações curtas, e é proposital:

1. RESERVA: UPDATE ... SET status='processando' WHERE id=? AND status='pendente'.
   O WHERE status='pendente' torna a operação um "compare-and-set" atômico: se
   duas execuções tentarem processar o mesmo material ao mesmo tempo, o
   Postgres trava a linha para a primeira; a segunda espera, reavalia o WHERE,
   não encontra mais 'pendente' e atualiza 0 linhas. Só uma processa.

   (Extração, chunking e embeddings rodam FORA de qualquer transação. Uma
   transação aberta durante segundos de CPU seguraria locks e um snapshot
   antigo, que impede o VACUUM de limpar versões mortas de linhas.)

2. GRAVAÇÃO: INSERT de todos os trechos + UPDATE status='concluido' numa
   transação só. Atomicidade (o "A" de ACID): ou tudo vira visível no COMMIT,
   ou nada. Se o 300º INSERT falhar, o ROLLBACK desfaz os 299 anteriores, e
   nenhuma outra conexão chegou a vê-los (isolamento: o que não foi commitado
   é invisível para os outros).

3. ERRO: se qualquer passo falhar, uma transação NOVA grava status='erro' e a
   mensagem. Precisa ser nova porque a anterior foi abortada: depois de um erro,
   o Postgres recusa qualquer comando até o ROLLBACK.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from sqlalchemy import func, insert, update
from sqlalchemy.orm import Session

from app.models import Material, Trecho
from app.servicos.chunking import dividir_em_trechos
from app.servicos.embeddings import Embedder
from app.servicos.pdf import extrair_paginas

log = logging.getLogger(__name__)

FabricaSessao = Callable[[], AbstractContextManager[Session]]


def processar_material(
    material_id: int, pasta_uploads: Path, fabrica: FabricaSessao, embedder: Embedder
) -> None:
    # 1. Reserva (compare-and-set)
    with fabrica() as s:
        reservado = s.execute(
            update(Material)
            .where(Material.id == material_id, Material.status == "pendente")
            .values(status="processando", erro_mensagem=None)
            .returning(Material.caminho_arquivo, Material.disciplina_id)
        ).one_or_none()
        s.commit()
    if reservado is None:
        log.info("material %s não está pendente; nada a fazer", material_id)
        return
    caminho, disciplina_id = reservado

    try:
        # Trabalho pesado, fora de transação
        paginas = extrair_paginas(pasta_uploads / caminho)
        trechos = dividir_em_trechos(paginas, embedder.tokenizar)
        vetores = embedder.embed_trechos([t.conteudo for t in trechos])

        # 2. Gravação atômica
        with fabrica() as s:
            try:
                # executemany: um único comando preparado, enviado com todas as linhas
                s.execute(
                    insert(Trecho),
                    [
                        {
                            "material_id": material_id,
                            "disciplina_id": disciplina_id,
                            "ordem": t.ordem,
                            "conteudo": t.conteudo,
                            "pagina": t.pagina,
                            "pagina_fim": t.pagina_fim,
                            "num_tokens": t.num_tokens,
                            "embedding": v,
                        }
                        for t, v in zip(trechos, vetores, strict=True)
                    ],
                )
                s.execute(
                    update(Material)
                    .where(Material.id == material_id, Material.status == "processando")
                    .values(
                        status="concluido", num_paginas=len(paginas), processado_em=func.now()
                    )
                )
                s.commit()
            except Exception:
                s.rollback()  # desfaz TODOS os INSERTs desta transação
                raise
        log.info("material %s: %s trechos gravados", material_id, len(trechos))

    except Exception as erro:
        log.exception("falha ao processar material %s", material_id)
        # 3. Registro do erro, em transação nova
        with fabrica() as s:
            s.execute(
                update(Material)
                .where(Material.id == material_id)
                .values(status="erro", erro_mensagem=str(erro)[:1000] or type(erro).__name__)
            )
            s.commit()
