"""Fila do dia e registro de revisões (SM-2), com controle otimista de concorrência."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.servicos.sm2 import EstadoSM2, calcular, proxima_revisao


class ErroRevisao(Exception):
    def __init__(self, status: int, mensagem: str, versao_atual: int | None = None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.versao_atual = versao_atual


# ------------------------------------------------------------- fila do dia

# "Hoje" no fuso do USUÁRIO, não do servidor nem em UTC:
#   :agora AT TIME ZONE fuso            -> hora de parede local (timestamp sem fuso)
#   date_trunc('day', ...)              -> meia-noite local de hoje
#   + interval '1 day'                  -> meia-noite local de amanhã
#   (...) AT TIME ZONE fuso             -> de volta a um instante (timestamptz)
# Um card entra na fila se vence ANTES da próxima meia-noite local.
SQL_FIM_DE_HOJE = """
    SELECT u.fuso_horario,
           (date_trunc('day', CAST(:agora AS timestamptz) AT TIME ZONE u.fuso_horario)
             + interval '1 day') AT TIME ZONE u.fuso_horario AS fim_de_hoje
    FROM usuarios AS u WHERE u.id = :usuario_id
"""

# Ordem por atraso: quem venceu há mais tempo primeiro (proxima_revisao ASC).
_COLUNAS = """
    r.flashcard_id, f.frente, f.verso, f.topico,
    r.disciplina_id, d.nome AS disciplina_nome,
    r.proxima_revisao, r.repeticoes, r.intervalo_dias, r.facilidade, r.versao,
    round(greatest(extract(epoch FROM CAST(:agora AS timestamptz) - r.proxima_revisao), 0)
          / 86400, 2) AS atraso_dias
"""

# Uma disciplina: o índice (disciplina_id, proxima_revisao) entrega as linhas já
# filtradas E em ordem; o LIMIT para depois de :limite entradas do índice.
SQL_FILA_DISCIPLINA = f"""
    SELECT {_COLUNAS}
    FROM revisoes AS r
    JOIN disciplinas AS d ON d.id = r.disciplina_id
    JOIN flashcards AS f ON f.id = r.flashcard_id
    WHERE r.disciplina_id = :disciplina_id
      AND d.usuario_id = :usuario_id
      AND r.proxima_revisao < :fim_de_hoje
    ORDER BY r.proxima_revisao, r.flashcard_id
    LIMIT :limite
"""

# Todas as disciplinas do usuário: o mesmo índice NÃO entrega em ordem de data as
# linhas de várias disciplinas juntas (ele ordena por disciplina primeiro). Um
# JOIN simples acabaria lendo todos os vencidos para depois ordenar. Com LATERAL,
# para CADA disciplina do usuário uma subconsulta pega os :limite mais atrasados
# pelo índice (top-N por grupo); depois só essas linhas (n_disciplinas x :limite)
# são ordenadas. Medido em docs/experimentos/fila-do-dia.md.
SQL_FILA_TODAS = f"""
    SELECT {_COLUNAS}
    FROM disciplinas AS d
    CROSS JOIN LATERAL (
        SELECT * FROM revisoes AS r2
        WHERE r2.disciplina_id = d.id AND r2.proxima_revisao < :fim_de_hoje
        ORDER BY r2.proxima_revisao, r2.flashcard_id
        LIMIT :limite
    ) AS r
    JOIN flashcards AS f ON f.id = r.flashcard_id
    WHERE d.usuario_id = :usuario_id
    ORDER BY r.proxima_revisao, r.flashcard_id
    LIMIT :limite
"""
# Duas consultas em vez de "AND (:disciplina_id IS NULL OR r.disciplina_id = :disciplina_id)":
# esse "OR com parâmetro opcional" atrapalha o planejador (num plano genérico ele
# não sabe qual lado do OR vale) e as duas filas pedem formas diferentes de consulta.


@dataclass
class Fila:
    fuso_horario: str
    fim_de_hoje: datetime
    cards: list


def fila_do_dia(
    session: Session,
    *,
    usuario_id: int,
    disciplina_id: int | None,
    limite: int,
    agora: datetime | None = None,
) -> Fila:
    agora = agora or datetime.now(UTC)
    fuso, fim_de_hoje = session.execute(
        text(SQL_FIM_DE_HOJE), {"agora": agora, "usuario_id": usuario_id}
    ).one()
    params = {"agora": agora, "usuario_id": usuario_id, "fim_de_hoje": fim_de_hoje, "limite": limite}
    if disciplina_id is None:
        cards = session.execute(text(SQL_FILA_TODAS), params).mappings().all()
    else:
        cards = session.execute(
            text(SQL_FILA_DISCIPLINA), params | {"disciplina_id": disciplina_id}
        ).mappings().all()
    return Fila(fuso, fim_de_hoje, cards)


# ---------------------------------------------------------- registrar revisão

SQL_LER_ESTADO = text("""
    SELECT r.facilidade, r.intervalo_dias, r.repeticoes, r.proxima_revisao, r.versao
    FROM revisoes AS r
    JOIN disciplinas AS d ON d.id = r.disciplina_id
    WHERE r.flashcard_id = :flashcard_id AND d.usuario_id = :usuario_id
""")

# Compare-and-set: só grava se a versão ainda for a que o cliente viu.
# Se outra requisição gravou antes, este UPDATE espera o lock da linha, reavalia
# o WHERE com a versão nova e atualiza 0 linhas.
SQL_ATUALIZAR_ESTADO = text("""
    UPDATE revisoes
    SET facilidade = :facilidade, intervalo_dias = :intervalo, repeticoes = :repeticoes,
        proxima_revisao = :proxima, ultima_revisao_em = :agora, versao = versao + 1
    WHERE flashcard_id = :flashcard_id AND versao = :versao
    RETURNING versao
""")

SQL_INSERIR_HISTORICO = text("""
    INSERT INTO historico_revisoes (
        flashcard_id, nota,
        facilidade_anterior, facilidade_nova, intervalo_anterior, intervalo_novo,
        repeticoes_anterior, repeticoes_nova, proxima_revisao_anterior, proxima_revisao_nova,
        revisado_em)
    VALUES (:flashcard_id, :nota, :facilidade_anterior, :facilidade, :intervalo_anterior,
            :intervalo, :repeticoes_anterior, :repeticoes, :proxima_anterior, :proxima, :agora)
    RETURNING id
""")


@dataclass
class ResultadoRevisao:
    flashcard_id: int
    nota: int
    anterior: EstadoSM2
    novo: EstadoSM2
    proxima_revisao: datetime
    versao: int
    historico_id: int


def _inserir_historico(session: Session, params: dict) -> int:
    return session.execute(SQL_INSERIR_HISTORICO, params).scalar_one()


def registrar_revisao(
    session: Session,
    *,
    usuario_id: int,
    flashcard_id: int,
    nota: int,
    versao: int,
    agora: datetime | None = None,
) -> ResultadoRevisao:
    """Aplica o SM-2 e grava estado + histórico numa transação só.

    Controle OTIMISTA de concorrência: nenhum lock é pedido na leitura; o
    UPDATE ... WHERE versao = :versao detecta se alguém mudou o card desde que o
    cliente o viu (duplo clique, duas abas) e, nesse caso, nada é gravado (409).
    """
    agora = agora or datetime.now(UTC)
    linha = session.execute(
        SQL_LER_ESTADO, {"flashcard_id": flashcard_id, "usuario_id": usuario_id}
    ).one_or_none()
    if linha is None:
        raise ErroRevisao(404, "flashcard não encontrado")
    if linha.versao != versao:  # atalho: conflito já visível, nem calcula
        raise ErroRevisao(409, "o card mudou desde que você o abriu", linha.versao)

    anterior = EstadoSM2(Decimal(linha.facilidade), linha.intervalo_dias, linha.repeticoes)
    novo = calcular(anterior, nota)
    proxima = proxima_revisao(agora, novo.intervalo)

    nova_versao = session.execute(
        SQL_ATUALIZAR_ESTADO,
        {
            "facilidade": novo.facilidade, "intervalo": novo.intervalo,
            "repeticoes": novo.repeticoes, "proxima": proxima, "agora": agora,
            "flashcard_id": flashcard_id, "versao": versao,
        },
    ).scalar_one_or_none()
    if nova_versao is None:
        # Outra requisição gravou entre a nossa leitura e o UPDATE.
        session.rollback()
        atual = session.execute(
            SQL_LER_ESTADO, {"flashcard_id": flashcard_id, "usuario_id": usuario_id}
        ).one()
        raise ErroRevisao(409, "esta revisão já foi registrada por outra requisição", atual.versao)

    try:
        historico_id = _inserir_historico(
            session,
            {
                "flashcard_id": flashcard_id, "nota": nota,
                "facilidade_anterior": anterior.facilidade, "facilidade": novo.facilidade,
                "intervalo_anterior": anterior.intervalo, "intervalo": novo.intervalo,
                "repeticoes_anterior": anterior.repeticoes, "repeticoes": novo.repeticoes,
                "proxima_anterior": linha.proxima_revisao, "proxima": proxima, "agora": agora,
            },
        )
        session.commit()  # estado + histórico, juntos
    except Exception:
        session.rollback()  # o UPDATE do estado também é desfeito
        raise
    return ResultadoRevisao(flashcard_id, nota, anterior, novo, proxima, nova_versao, historico_id)
