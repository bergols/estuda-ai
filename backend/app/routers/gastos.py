from fastapi import APIRouter
from sqlalchemy import text

from app.deps import SessionDep, UsuarioAtual
from app.schemas import GastoMensal

router = APIRouter(tags=["gastos"])

# Gasto por disciplina e por mês.
# - date_trunc('month', ...) "arredonda" o instante para o 1º dia do mês.
# - AT TIME ZONE 'America/Sao_Paulo': criado_em é timestamptz (um instante, em
#   UTC). Uma geração às 23h30 de 31/10 em Brasília é 02h30 de 01/11 em UTC.
#   Sem converter para o fuso do usuário, ela cairia no mês errado.
# - LEFT JOIN: gerações de disciplinas apagadas (disciplina_id NULL) continuam
#   aparecendo, com nome NULL, em vez de sumirem do relatório.
# - coalesce(sum(custo_usd), 0): SUM ignora NULLs (custo desconhecido) e devolve
#   NULL se TODOS forem NULL.
# - O índice (usuario_id, criado_em) atende o WHERE usuario_id = :u.
SQL_GASTOS = text("""
    SELECT g.disciplina_id,
           d.nome AS disciplina_nome,
           date_trunc('month', g.criado_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
           count(*) AS geracoes,
           sum(g.tokens_entrada) AS tokens_entrada,
           sum(g.tokens_saida) AS tokens_saida,
           coalesce(sum(g.custo_usd), 0) AS custo_usd
    FROM geracoes AS g
    LEFT JOIN disciplinas AS d ON d.id = g.disciplina_id
    WHERE g.usuario_id = :usuario_id
    GROUP BY g.disciplina_id, d.nome, mes
    ORDER BY mes DESC, custo_usd DESC, g.disciplina_id
""")


@router.get("/gastos", response_model=list[GastoMensal])
def gastos(usuario: UsuarioAtual, session: SessionDep):
    """Gasto com o LLM do usuário atual, por disciplina e por mês (todas as
    gerações, inclusive as que falharam: a chamada foi cobrada)."""
    return session.execute(SQL_GASTOS, {"usuario_id": usuario.id}).mappings().all()
