"""Rate limiting (janela fixa, no Postgres) e cota diária de gerações de IA."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class Excedido(Exception):
    mensagem: str
    tentar_de_novo_em_s: int


# date_bin(intervalo, instante, origem) "arredonda para baixo" o instante para o
# início da sua janela: com intervalo de 15 min, 10:07 e 10:14 caem em 10:00.
# UPSERT atômico: o ON CONFLICT trava a linha da janela; uma requisição simultânea
# espera e soma em cima do valor já incrementado. RETURNING devolve a contagem
# nova e quantos segundos faltam para a janela acabar, numa ida ao banco só.
SQL_CONTAR = text("""
    INSERT INTO limites_taxa (chave, janela_inicio, contagem)
    VALUES (:chave, date_bin(:janela, now(), timestamptz '2000-01-01 00:00+00'), 1)
    ON CONFLICT (chave, janela_inicio)
    DO UPDATE SET contagem = limites_taxa.contagem + 1
    RETURNING contagem,
              ceil(extract(epoch FROM janela_inicio + :janela - now()))::int AS restante_s
""")

# Janelas vencidas não servem para nada: a limpeza roda junto, no mesmo COMMIT.
SQL_LIMPAR = text("DELETE FROM limites_taxa WHERE janela_inicio < now() - interval '1 day'")


def consumir(session: Session, chave: str, limite: int, janela: timedelta) -> None:
    """Conta mais uma requisição para `chave` na janela atual; Excedido se passou do limite.

    O COMMIT é imediato e separado do resto da requisição: uma tentativa de login
    com senha errada precisa CONTAR mesmo que a requisição termine em erro.
    """
    contagem, restante = session.execute(SQL_CONTAR, {"chave": chave, "janela": janela}).one()
    if contagem == 1:
        session.execute(SQL_LIMPAR)
    session.commit()
    if contagem > limite:
        raise Excedido("muitas requisições; tente de novo mais tarde", max(1, restante))


SQL_CONTAGEM_ATUAL = text("""
    SELECT contagem,
           ceil(extract(epoch FROM janela_inicio + :janela - now()))::int AS restante_s
    FROM limites_taxa
    WHERE chave = :chave
      AND janela_inicio = date_bin(:janela, now(), timestamptz '2000-01-01 00:00+00')
""")


def verificar(session: Session, chave: str, limite: int, janela: timedelta) -> None:
    """Como consumir(), mas sem contar: só confere se a janela atual já estourou."""
    linha = session.execute(SQL_CONTAGEM_ATUAL, {"chave": chave, "janela": janela}).one_or_none()
    if linha is not None and linha.contagem >= limite:
        raise Excedido("muitas tentativas; tente de novo mais tarde", max(1, linha.restante_s))


# Cota diária: conta na própria auditoria (geracoes), a fonte da verdade do gasto.
# "Hoje" começa à meia-noite NO FUSO DO USUÁRIO. O limite é uma subconsulta escalar
# (InitPlan) para a condição usar o índice (usuario_id, criado_em) como Index Cond.
SQL_GERACOES_HOJE = text("""
    WITH inicio AS (
        SELECT (date_trunc('day', now() AT TIME ZONE fuso_horario)) AT TIME ZONE fuso_horario AS dia,
               fuso_horario
        FROM usuarios WHERE id = :usuario_id
    )
    SELECT (SELECT count(*) FROM geracoes
            WHERE usuario_id = :usuario_id AND criado_em >= (SELECT dia FROM inicio)) AS hoje,
           ceil(extract(epoch FROM (SELECT dia FROM inicio) + interval '1 day' - now()))::int
               AS ate_meia_noite_s
""")


def verificar_cota_diaria(session: Session, usuario_id: int, limite: int) -> None:
    """Excedido se o usuário já fez `limite` gerações hoje.

    Limite "suave": entre esta contagem e a gravação da geração há uma janela em que
    duas requisições simultâneas podem passar juntas (check-then-act). Para um teto
    de custo diário, passar 1 ou 2 do limite é aceitável; o rate limit por minuto,
    que é atômico, segura rajadas.
    """
    hoje, ate_meia_noite = session.execute(SQL_GERACOES_HOJE, {"usuario_id": usuario_id}).one()
    if hoje >= limite:
        raise Excedido(
            f"limite diário de {limite} gerações com IA atingido; volta à meia-noite",
            max(1, ate_meia_noite),
        )
