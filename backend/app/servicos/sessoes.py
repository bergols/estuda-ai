"""Sincronização das sessões de estudo do app desktop (fase 7).

O app grava cada sessão, pausa e evento num SQLite local e manda ao servidor tudo o
que ainda não teve confirmação. Pode mandar a mesma coisa várias vezes: depois de
um timeout (o servidor gravou, mas a resposta se perdeu), depois de horas offline,
depois de reabrir o app. Este módulo torna esse reenvio INOFENSIVO:

1. Sessão: INSERT ... ON CONFLICT (usuario_id, chave) DO UPDATE ... WHERE. A primeira
   vez insere; as próximas só podem mudar o status de em_andamento para concluida ou
   abandonada (nunca o contrário, mesmo que os envios cheguem fora de ordem). O resto
   da linha é do primeiro envio.
2. Pausas e eventos: INSERT ... SELECT FROM unnest(...) ON CONFLICT DO NOTHING, um
   comando para todas as pausas da sessão (uma ida ao banco, não N).
3. Cada sessão do lote num SAVEPOINT: uma sessão que viola uma regra do banco (ex.:
   52/17 com 50 minutos) é RECUSADA sozinha, e as outras do lote são gravadas. Sem
   isso, uma sessão inválida travaria o lote inteiro para sempre: o app reenviaria,
   o servidor recusaria tudo de novo ("mensagem envenenada").

Nada de "SELECT para ver se já existe e depois INSERT": entre os dois comandos, outro
envio (o outro computador, uma segunda tentativa) poderia inserir a mesma chave. O
ON CONFLICT decide no próprio comando, protegido pelo índice único.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import constraint_violada
from app.schemas import LoteSessoes, ResultadoSessao, SessaoEnvio

# Relógio do computador adiantado demais: recusa em vez de gravar um "futuro"
TOLERANCIA_RELOGIO = timedelta(hours=24)

# A sessão e o "veredito" do upsert num comando só.
#
# - A disciplina entra por subconsulta com o dono: se não existir ou for de outra pessoa,
#   vira NULL (a sessão é gravada sem ela; o tempo estudado não se perde). A FK composta
#   continua sendo a garantia final no banco.
# - DO UPDATE ... WHERE: só atualiza se a linha gravada ainda está em andamento E o envio
#   novo a termina. Quando o WHERE é falso, o RETURNING não devolve nada.
# - xmax = 0: no Postgres, uma linha recém-INSERIDA tem xmax 0; uma linha que veio do
#   DO UPDATE tem o id da transação que a atualizou. Assim um comando só diz se inseriu
#   ou atualizou.
# - O 2o SELECT (UNION ALL) cobre o "sem mudança": a linha já existia e o WHERE recusou a
#   atualização. Ele lê a tabela como estava no INÍCIO deste comando (o snapshot do
#   comando, no isolamento READ COMMITTED), por isso só é usado quando a CTE não
#   devolveu nada.
#
# Corrida que o teste de concorrência encontrou: se OUTRO envio da mesma chave inseriu
# a linha mas ainda não deu COMMIT, este comando espera no índice único; quando o outro
# confirma, o ON CONFLICT vê a linha nova, o WHERE recusa (as duas "em andamento"), o
# RETURNING vem vazio... e o 2o SELECT também, porque o snapshot dele é de ANTES do
# COMMIT do outro. Resultado: nenhuma linha. Ver SQL_RELER abaixo.
SQL_SESSAO = text(
    """
    WITH gravada AS (
        INSERT INTO sessoes_estudo (
            usuario_id, disciplina_id, chave, metodo, foco_min, pausa_min, ciclos,
            pausa_longa_min, ciclos_ate_pausa_longa, meta, sistema, status,
            iniciada_em, terminada_em
        )
        VALUES (
            :usuario_id,
            (SELECT d.id FROM disciplinas AS d
             WHERE d.id = :disciplina_id AND d.usuario_id = :usuario_id),
            :chave, :metodo, :foco_min, :pausa_min, :ciclos,
            :pausa_longa_min, :ciclos_ate_pausa_longa, :meta, :sistema, :status,
            :iniciada_em, :terminada_em
        )
        ON CONFLICT (usuario_id, chave) DO UPDATE
            SET status = EXCLUDED.status,
                terminada_em = EXCLUDED.terminada_em
            WHERE sessoes_estudo.status = 'em_andamento'
              AND EXCLUDED.status <> 'em_andamento'
        RETURNING id, disciplina_id, (xmax = 0) AS inserida
    )
    SELECT id, disciplina_id, CASE WHEN inserida THEN 'criada' ELSE 'atualizada' END AS resultado
    FROM gravada
    UNION ALL
    SELECT s.id, s.disciplina_id, 'sem_mudanca'
    FROM sessoes_estudo AS s
    WHERE s.usuario_id = :usuario_id AND s.chave = :chave
      AND NOT EXISTS (SELECT FROM gravada)
    """
)

# A releitura da corrida acima: um comando NOVO ganha um snapshot novo e enxerga a linha
# que o outro envio acabou de confirmar.
SQL_RELER = text(
    """
    SELECT id, disciplina_id, 'sem_mudanca'
    FROM sessoes_estudo
    WHERE usuario_id = :usuario_id AND chave = :chave
    """
)

# Todas as pausas da sessão num comando: o psycopg manda cada lista Python como um array
# do Postgres, e unnest() as "abre" em linhas, coluna a coluna (arrays paralelos).
SQL_PAUSAS = text(
    """
    INSERT INTO pausas_sessao (sessao_id, chave, tipo, iniciada_em, terminada_em)
    SELECT :sessao_id, p.chave, p.tipo, p.iniciada_em, p.terminada_em
    FROM unnest(
        CAST(:chaves AS uuid[]), CAST(:tipos AS text[]),
        CAST(:inicios AS timestamptz[]), CAST(:fins AS timestamptz[])
    ) AS p(chave, tipo, iniciada_em, terminada_em)
    ON CONFLICT (sessao_id, chave) DO NOTHING
    RETURNING id
    """
)

SQL_EVENTOS = text(
    """
    INSERT INTO eventos_foco (sessao_id, chave, tipo, ocorrido_em, duracao_s, detalhe)
    SELECT :sessao_id, e.chave, e.tipo, e.ocorrido_em, e.duracao_s, e.detalhe
    FROM unnest(
        CAST(:chaves AS uuid[]), CAST(:tipos AS text[]), CAST(:ocorridos AS timestamptz[]),
        CAST(:duracoes AS integer[]), CAST(:detalhes AS text[])
    ) AS e(chave, tipo, ocorrido_em, duracao_s, detalhe)
    ON CONFLICT (sessao_id, chave) DO NOTHING
    RETURNING id
    """
)


class SessaoRecusada(Exception):
    pass


@dataclass
class _Gravada:
    id: int
    disciplina_id: int | None
    resultado: str


def sincronizar(
    session: Session, *, usuario_id: int, lote: LoteSessoes, agora: datetime | None = None
) -> list[ResultadoSessao]:
    agora = agora or datetime.now(UTC)
    resultados = []
    for item in lote.sessoes:
        try:
            # SAVEPOINT: um erro desfaz só esta sessão, não as anteriores do lote
            with session.begin_nested():
                resultados.append(_gravar(session, usuario_id, item, agora))
        except IntegrityError as erro:
            resultados.append(_recusada(item, constraint_violada(erro) or "regra do banco"))
        except SessaoRecusada as erro:
            resultados.append(_recusada(item, str(erro)))
    session.commit()
    return resultados


def _recusada(item: SessaoEnvio, motivo: str) -> ResultadoSessao:
    return ResultadoSessao(chave=item.chave, resultado="recusada", erro=motivo)


def _gravar(session: Session, usuario_id: int, item: SessaoEnvio, agora: datetime) -> ResultadoSessao:
    _conferir_relogio(item, agora)
    parametros: dict[str, Any] = item.model_dump(exclude={"pausas", "eventos"})
    linha = session.execute(SQL_SESSAO, {**parametros, "usuario_id": usuario_id}).one_or_none()
    if linha is None:  # a corrida descrita em SQL_SESSAO
        linha = session.execute(SQL_RELER, {"usuario_id": usuario_id, "chave": item.chave}).one()
    gravada = _Gravada(*linha)

    pausas_novas = eventos_novos = 0
    if item.pausas:
        pausas_novas = len(
            session.execute(
                SQL_PAUSAS,
                {
                    "sessao_id": gravada.id,
                    "chaves": [p.chave for p in item.pausas],
                    "tipos": [p.tipo for p in item.pausas],
                    "inicios": [p.iniciada_em for p in item.pausas],
                    "fins": [p.terminada_em for p in item.pausas],
                },
            ).all()
        )
    if item.eventos:
        eventos_novos = len(
            session.execute(
                SQL_EVENTOS,
                {
                    "sessao_id": gravada.id,
                    "chaves": [e.chave for e in item.eventos],
                    "tipos": [e.tipo for e in item.eventos],
                    "ocorridos": [e.ocorrido_em for e in item.eventos],
                    "duracoes": [e.duracao_s for e in item.eventos],
                    "detalhes": [e.detalhe for e in item.eventos],
                },
            ).all()
        )

    return ResultadoSessao(
        chave=item.chave,
        resultado=gravada.resultado,
        id=gravada.id,
        pausas_novas=pausas_novas,
        eventos_novos=eventos_novos,
        disciplina_descartada=item.disciplina_id is not None and gravada.disciplina_id is None,
    )


def _conferir_relogio(item: SessaoEnvio, agora: datetime) -> None:
    limite = agora + TOLERANCIA_RELOGIO
    instantes = [item.iniciada_em, item.terminada_em]
    instantes += [p.terminada_em for p in item.pausas] + [e.ocorrido_em for e in item.eventos]
    if any(i is not None and i > limite for i in instantes):
        raise SessaoRecusada("data no futuro: confira o relógio do computador")


SQL_LISTAR = text(
    """
    SELECT v.id, s.chave, v.disciplina_id, d.nome AS disciplina, v.metodo, v.status, s.meta,
           v.sistema, v.iniciada_em, v.terminada_em, v.dia, v.duracao_planejada_s,
           v.duracao_real_s, v.pausas_s, v.fora_s, v.interrupcoes, v.emergencias,
           v.foco_efetivo_s
    FROM vw_sessoes_foco AS v
    JOIN sessoes_estudo AS s ON s.id = v.id
    LEFT JOIN disciplinas AS d ON d.id = v.disciplina_id
    WHERE v.usuario_id = :usuario_id
    ORDER BY v.iniciada_em DESC
    LIMIT :limite
    """
)


def listar(session: Session, *, usuario_id: int, limite: int) -> list[dict]:
    return [dict(r) for r in session.execute(SQL_LISTAR, {"usuario_id": usuario_id, "limite": limite}).mappings()]
