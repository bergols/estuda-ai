"""Flashcards e questões escritos pelo Claude Code, gravados pelas mesmas regras do app.

O app gera conteúdo com a API da Anthropic (cobrada por token). Este script é a outra
porta: numa sessão do Claude Code (coberta pela assinatura do autor), o Claude lê os
trechos de um material, escreve os cards/questões e grava com `importar`. As regras de
gravação são as MESMAS da geração pela API (app/servicos/gerar.py: persistir_*):
duplicatas descartadas pelo embedding, ligação com os trechos de origem, questão com 4
alternativas e exatamente 1 correta, e uma linha de auditoria em geracoes com o modelo
"claude-code-assinatura" (custo zero).

Roda dentro do container do backend (o banco e o modelo de embeddings estão lá):

    D="docker compose -f docker-compose.prod.yml exec -T backend python -m scripts.conteudo_claude"
    $D materiais                                  # disciplinas e materiais da conta
    $D trechos --material 7                       # texto do material, com o id de cada trecho
    $D trechos --disciplina 3 --tema "índices"    # trechos mais relevantes para um tema
    $D importar --simular < lote.json             # valida e mostra o que faria, sem gravar
    $D importar < lote.json                       # grava

Formato do lote (JSON):

    {
      "disciplina_id": 3,
      "flashcards": [
        {"frente": "...", "verso": "...", "topico": "Índices", "trechos": [101, 102]}
      ],
      "questoes": [
        {"enunciado": "...", "alternativas": ["...", "...", "...", "..."], "correta": "B",
         "explicacao": "...", "dificuldade": 3, "topico": "Índices", "trechos": [101]}
      ]
    }

Os trechos citados precisam ser da disciplina do lote (senão nada é gravado).
--email escolhe a conta; sem ele, usa a única conta que existir.
"""

import argparse
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Disciplina, Usuario
from app.servicos import auditoria, busca
from app.servicos.auth import consulta_por_email
from app.servicos.embeddings import Embedder, get_embedder
from app.servicos.gerar import CardNovo, QuestaoNova, persistir_flashcards, persistir_questoes
from app.servicos.llm import Uso

MODELO = "claude-code-assinatura"


# ------------------------------------------------------------------ formato do lote


class CardEntrada(BaseModel):
    frente: str = Field(min_length=3, max_length=300)
    verso: str = Field(min_length=1, max_length=1000)
    topico: str | None = Field(default=None, max_length=60)
    trechos: list[int] = Field(min_length=1)


class QuestaoEntrada(BaseModel):
    enunciado: str = Field(min_length=10, max_length=1000)
    alternativas: list[str] = Field(min_length=4, max_length=4)
    correta: Literal["A", "B", "C", "D"]
    explicacao: str = Field(min_length=1, max_length=2000)
    dificuldade: int = Field(ge=1, le=5)
    topico: str | None = Field(default=None, max_length=60)
    trechos: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def alternativas_distintas(self):
        textos = [a.strip().casefold() for a in self.alternativas]
        if len(set(textos)) != len(textos):
            raise ValueError("alternativas repetidas")
        return self


class Lote(BaseModel):
    disciplina_id: int
    flashcards: list[CardEntrada] = []
    questoes: list[QuestaoEntrada] = []

    @model_validator(mode="after")
    def nao_vazio(self):
        if not self.flashcards and not self.questoes:
            raise ValueError("o lote não tem flashcards nem questões")
        return self


# ------------------------------------------------------------------------- conta


def escolher_usuario(session: Session, email: str | None) -> Usuario:
    if email:
        # lower(email) = ..., como o login: ILIKE trataria % e _ como curingas
        usuario = session.scalar(consulta_por_email(email))
        if usuario is None:
            sys.exit(f"Não existe conta com o e-mail {email}.")
        return usuario
    usuarios = session.scalars(select(Usuario).order_by(Usuario.id)).all()
    if len(usuarios) != 1:
        emails = ", ".join(u.email for u in usuarios) or "(nenhuma)"
        sys.exit(f"Há {len(usuarios)} contas ({emails}); use --email.")
    return usuarios[0]


def disciplina_da_conta(session: Session, usuario: Usuario, disciplina_id: int) -> Disciplina:
    disciplina = session.scalar(
        select(Disciplina).where(Disciplina.id == disciplina_id, Disciplina.usuario_id == usuario.id)
    )
    if disciplina is None:
        sys.exit(f"A disciplina {disciplina_id} não existe nesta conta.")
    return disciplina


# ---------------------------------------------------------------------- comandos

SQL_MATERIAIS = text("""
    SELECT d.id AS disciplina_id, d.nome AS disciplina, m.id AS material_id, m.titulo,
           m.status, m.num_paginas,
           (SELECT count(*) FROM trechos t WHERE t.material_id = m.id) AS trechos,
           (SELECT count(*) FROM flashcards f WHERE f.disciplina_id = d.id) AS cards_na_disciplina
    FROM disciplinas d
    LEFT JOIN materiais m ON m.disciplina_id = d.id
    WHERE d.usuario_id = :usuario_id
    ORDER BY d.nome, m.criado_em
""")


def cmd_materiais(session: Session, usuario: Usuario, _args) -> None:
    print(f"Conta: {usuario.email}")
    atual = None
    for linha in session.execute(SQL_MATERIAIS, {"usuario_id": usuario.id}):
        if linha.disciplina_id != atual:
            atual = linha.disciplina_id
            print(f"\n[disciplina {linha.disciplina_id}] {linha.disciplina} "
                  f"({linha.cards_na_disciplina} flashcards)")
        if linha.material_id is not None:
            print(f"  - material {linha.material_id}: {linha.titulo} | {linha.status} | "
                  f"{linha.num_paginas or '?'} p. | {linha.trechos} trechos")


SQL_TRECHOS_MATERIAL = text("""
    SELECT t.id AS trecho_id, t.pagina, t.pagina_fim, t.conteudo, m.titulo AS material_titulo
    FROM trechos t
    JOIN materiais m ON m.id = t.material_id
    JOIN disciplinas d ON d.id = t.disciplina_id
    WHERE t.material_id = :material_id AND d.usuario_id = :usuario_id
    ORDER BY t.ordem
    LIMIT :limite OFFSET :inicio
""")


def _imprimir_trecho(linha) -> None:
    pagina = ""
    if linha.pagina is not None:
        fim = f"–{linha.pagina_fim}" if linha.pagina_fim and linha.pagina_fim != linha.pagina else ""
        pagina = f" p. {linha.pagina}{fim}"
    print(f"\n### trecho {linha.trecho_id} ({linha.material_titulo}{pagina})\n{linha.conteudo}")


def cmd_trechos(session: Session, usuario: Usuario, args) -> None:
    if args.material:
        linhas = session.execute(SQL_TRECHOS_MATERIAL, {
            "material_id": args.material, "usuario_id": usuario.id,
            "limite": args.limite, "inicio": args.inicio,
        }).all()
    else:
        disciplina = disciplina_da_conta(session, usuario, args.disciplina)
        embedder = get_embedder()
        linhas = busca.buscar_hibrida(
            session, disciplina.id, args.tema, embedder.embed_consulta(args.tema), args.limite
        )
    if not linhas:
        sys.exit("Nenhum trecho encontrado (material de outra conta, sem processar, ou tema sem resultado).")
    for linha in linhas:
        _imprimir_trecho(linha)
    print(f"\n({len(linhas)} trechos)")


class ErroLote(Exception):
    pass


def importar_lote(session: Session, usuario: Usuario, lote: Lote, embedder: Embedder) -> list[str]:
    """Grava o lote na transação atual (sem COMMIT) e devolve o resumo.
    ErroLote se a disciplina não é da conta ou algum trecho citado não é dela."""
    disciplina = session.scalar(
        select(Disciplina).where(Disciplina.id == lote.disciplina_id, Disciplina.usuario_id == usuario.id)
    )
    if disciplina is None:
        raise ErroLote(f"a disciplina {lote.disciplina_id} não existe nesta conta")

    # Todo trecho citado tem de ser desta disciplina (e, portanto, desta conta)
    citados = {t for item in [*lote.flashcards, *lote.questoes] for t in item.trechos}
    validos = set(session.scalars(
        text("SELECT id FROM trechos WHERE disciplina_id = :d AND id = ANY(:ids)"),
        {"d": disciplina.id, "ids": list(citados)},
    ))
    if faltando := sorted(citados - validos):
        raise ErroLote(f"trechos que não são da disciplina {disciplina.id}: {faltando}")

    resumo = [f"Disciplina: {disciplina.nome}"]
    # Uma transação para o lote inteiro: ou grava tudo, ou nada
    if lote.flashcards:
        geracao = auditoria.registrar(
            session, usuario_id=usuario.id, disciplina_id=disciplina.id, tipo="flashcards",
            modelo=MODELO, uso=Uso(chamadas=1), duracao_ms=0,
        )
        criados, descartados = persistir_flashcards(
            session, disciplina_id=disciplina.id, geracao_id=geracao.id, embedder=embedder,
            cards=[CardNovo(c.frente, c.verso, c.topico, c.trechos) for c in lote.flashcards],
        )
        resumo.append(f"{len(criados)} flashcards criados")
        for d in descartados:
            resumo.append(f"  descartado (duplicata {d.similaridade:.0%}): {d.frente!r} ≈ {d.parecido_com_frente!r}")
    if lote.questoes:
        geracao = auditoria.registrar(
            session, usuario_id=usuario.id, disciplina_id=disciplina.id, tipo="questoes",
            modelo=MODELO, uso=Uso(chamadas=1), duracao_ms=0,
        )
        criadas = persistir_questoes(
            session, disciplina_id=disciplina.id, geracao_id=geracao.id,
            questoes=[QuestaoNova(q.enunciado, q.alternativas, q.correta, q.explicacao,
                                  q.dificuldade, q.topico, q.trechos) for q in lote.questoes],
        )
        resumo.append(f"{len(criadas)} questões criadas")
    return resumo


def cmd_importar(session: Session, usuario: Usuario, args) -> None:
    try:
        lote = Lote.model_validate_json(sys.stdin.read())
        resumo = importar_lote(session, usuario, lote, get_embedder())
    except ValidationError as erro:
        sys.exit(f"Lote inválido:\n{erro}")
    except ErroLote as erro:
        session.rollback()
        sys.exit(f"{erro}. Nada foi gravado.")
    if args.simular:
        # Confere as regras adiadas (questão com 1 correta) antes de desfazer
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        session.rollback()
        print("SIMULAÇÃO (nada gravado):")
    else:
        session.commit()
        print("GRAVADO:")
    print("\n".join(resumo))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", help="conta (padrão: a única que existir)")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("materiais", help="disciplinas e materiais da conta")
    p_trechos = sub.add_parser("trechos", help="texto de um material ou de um tema")
    grupo = p_trechos.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--material", type=int)
    grupo.add_argument("--disciplina", type=int)
    p_trechos.add_argument("--tema", help="com --disciplina: busca híbrida pelo tema")
    p_trechos.add_argument("--limite", type=int, default=200)
    p_trechos.add_argument("--inicio", type=int, default=0, help="pular os N primeiros trechos")
    p_importar = sub.add_parser("importar", help="grava o lote JSON lido da entrada padrão")
    p_importar.add_argument("--simular", action="store_true", help="valida e desfaz, sem gravar")
    args = parser.parse_args()
    if args.comando == "trechos" and args.disciplina and not args.tema:
        parser.error("--disciplina precisa de --tema")

    with SessionLocal() as session:
        usuario = escolher_usuario(session, args.email)
        {"materiais": cmd_materiais, "trechos": cmd_trechos, "importar": cmd_importar}[args.comando](
            session, usuario, args
        )


if __name__ == "__main__":
    main()
