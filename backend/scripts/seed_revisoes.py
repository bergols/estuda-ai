"""Simula algumas semanas de estudo de um aluno, para a fase 5 ter dados realistas.

    docker compose exec backend python -m scripts.seed_revisoes [--dias 42]

Cria o usuário estudante@estuda-ai.local (apagando o anterior e, em cascata, tudo
dele) com 4 disciplinas, 5 tópicos cada, e simula dia a dia, no fuso de São Paulo:
- dias de estudo (mais faltas no fim de semana: as faltas geram ATRASO);
- cards novos entrando aos poucos (até 12 por disciplina por dia de estudo);
- revisão dos cards vencidos, do mais atrasado ao menos (limite de 80 por dia),
  com notas sorteadas a partir da dificuldade do tópico, do atraso e da
  maturidade do card, e o SM-2 de app/servicos/sm2.py aplicado a cada nota;
- algumas questões de múltipla escolha respondidas por dia, com um distrator
  "mais tentador" por questão (útil na análise de distratores da fase 5).

Técnicas de carga em massa:
1. COPY dos cards. COPY também dispara triggers de linha, então
   trg_flashcards_criar_estado cria o estado de cada card.
2. COPY do histórico (só INSERT, como o histórico exige).
3. Estado final: COPY para uma TABELA TEMPORÁRIA e um único
   UPDATE revisoes ... FROM estado_final, em vez de um UPDATE por card.
Tudo numa transação: se algo falhar, o banco fica como estava.
"""

import argparse
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.servicos.sm2 import EstadoSM2, calcular, proxima_revisao
from scripts.seed_experimento import url_psycopg

EMAIL = "estudante@estuda-ai.local"
FUSO = ZoneInfo("America/Sao_Paulo")

DISCIPLINAS = {
    "Banco de Dados": ["Índices", "Transações", "Normalização", "SQL", "Busca vetorial"],
    "Estruturas de Dados": ["Árvores", "Grafos", "Hashing", "Listas", "Ordenação"],
    "Redes de Computadores": ["TCP/IP", "Roteamento", "DNS", "HTTP", "Camada de enlace"],
    "Cálculo II": ["Integrais", "Séries", "Derivadas parciais", "Integrais múltiplas", "Polares"],
}
CARDS_POR_TOPICO = 24
QUESTOES_POR_TOPICO = 3
NOVOS_POR_DIA = 12
LIMITE_DIARIO = 80


@dataclass
class Card:
    disciplina: str
    topico: str
    frente: str
    dificuldade: float  # 0 = fácil, 1 = difícil (escondida do SM-2)
    criado_em: datetime | None = None
    estado: EstadoSM2 = field(default_factory=EstadoSM2)
    proxima: datetime | None = None
    ultima: datetime | None = None
    versao: int = 0


@dataclass
class Questao:
    disciplina: str
    topico: str
    enunciado: str
    dificuldade: int  # 1..5
    correta: int  # índice 0..3


def momento_local(dia: date, hora: float) -> datetime:
    """Um horário de parede em São Paulo, devolvido como instante UTC."""
    h, m = int(hora), int((hora % 1) * 60)
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=FUSO).astimezone(UTC)


def sortear_nota(rng: random.Random, card: Card, agora: datetime) -> int:
    atraso = max(0.0, (agora - card.proxima).total_seconds() / 86400)
    p_lembrar = 0.95 - 0.6 * card.dificuldade - 0.01 * min(atraso, 30) + 0.02 * min(
        card.estado.repeticoes, 5
    )
    p_lembrar = min(0.98, max(0.05, p_lembrar))
    if rng.random() < p_lembrar:
        facil = 1 - card.dificuldade
        return rng.choices([3, 4, 5], weights=[1.0, 1.5 + facil, 0.5 + 2 * facil])[0]
    return rng.choices([0, 1, 2], weights=[0.2, 0.4, 0.4])[0]


def simular(dias: int, rng: random.Random):
    hoje_local = datetime.now(FUSO).date()
    inicio = hoje_local - timedelta(days=dias)

    dificuldade_topico = {
        (d, t): rng.uniform(0.1, 0.65) for d, topicos in DISCIPLINAS.items() for t in topicos
    }
    nao_introduzidos: dict[str, list[Card]] = {}
    for d, topicos in DISCIPLINAS.items():
        cards = [
            Card(d, t, f"{t}: pergunta {i + 1}",
                 min(0.95, max(0.0, dificuldade_topico[(d, t)] + rng.gauss(0, 0.12))))
            for t in topicos for i in range(CARDS_POR_TOPICO)
        ]
        rng.shuffle(cards)
        nao_introduzidos[d] = cards
    questoes = [
        Questao(d, t, f"{t}: questão {i + 1}", rng.randint(1, 5), rng.randrange(4))
        for d, topicos in DISCIPLINAS.items() for t in topicos for i in range(QUESTOES_POR_TOPICO)
    ]

    ativos: list[Card] = []
    historico: list[tuple] = []  # (card, nota, anterior, novo, proxima_anterior, proxima_nova, quando)
    tentativas: list[tuple] = []  # (questao, alternativa_escolhida, quando, tempo_ms)

    for n in range(dias):
        dia = inicio + timedelta(days=n)
        fim_de_semana = dia.weekday() >= 5
        if rng.random() > (0.6 if fim_de_semana else 0.88):
            continue  # faltou: os vencidos acumulam atraso
        agora = momento_local(dia, rng.uniform(18.5, 22.5))

        for d in DISCIPLINAS:  # cards novos do dia
            for _ in range(min(NOVOS_POR_DIA, len(nao_introduzidos[d]))):
                card = nao_introduzidos[d].pop()
                card.criado_em = agora
                card.proxima = agora
                ativos.append(card)

        fim_do_dia = momento_local(dia + timedelta(days=1), 0)
        vencidos = sorted((c for c in ativos if c.proxima < fim_do_dia), key=lambda c: c.proxima)
        for card in vencidos[:LIMITE_DIARIO]:
            agora += timedelta(seconds=rng.randint(15, 90))
            nota = sortear_nota(rng, card, agora)
            novo = calcular(card.estado, nota)
            proxima_nova = proxima_revisao(agora, novo.intervalo)
            historico.append((card, nota, card.estado, novo, card.proxima, proxima_nova, agora))
            card.estado, card.proxima, card.ultima = novo, proxima_nova, agora
            card.versao += 1

        for questao in rng.sample(questoes, rng.randint(2, 6)):
            agora += timedelta(seconds=rng.randint(30, 180))
            p_acerto = 0.92 - 0.1 * questao.dificuldade
            if rng.random() < p_acerto:
                escolhida = questao.correta
            else:  # o 1o distrator é o mais tentador
                erradas = [i for i in range(4) if i != questao.correta]
                escolhida = rng.choices(erradas, weights=[0.6, 0.25, 0.15])[0]
            tentativas.append((questao, escolhida, agora, rng.randint(8_000, 90_000)))

    return ativos, historico, questoes, tentativas


def main(dias: int) -> None:
    rng = random.Random(2026)
    t0 = time.perf_counter()
    cards, historico, questoes, tentativas = simular(dias, rng)

    with psycopg.connect(url_psycopg()) as conn:  # uma transação até o commit()
        cur = conn.cursor()
        cur.execute("DELETE FROM usuarios WHERE email = %s", (EMAIL,))
        usuario_id = cur.execute(
            "INSERT INTO usuarios (nome, email, fuso_horario) "
            "VALUES ('Estudante', %s, 'America/Sao_Paulo') RETURNING id",
            (EMAIL,),
        ).fetchone()[0]
        disciplina_id = {}
        for nome in DISCIPLINAS:
            disciplina_id[nome] = cur.execute(
                "INSERT INTO disciplinas (usuario_id, nome) VALUES (%s, %s) RETURNING id",
                (usuario_id, nome),
            ).fetchone()[0]

        # 1. cards (o trigger cria o estado de cada um)
        with cur.copy(
            "COPY flashcards (disciplina_id, frente, verso, topico, origem, criado_em) FROM STDIN"
        ) as copy:
            for c in cards:
                copy.write_row((disciplina_id[c.disciplina], c.frente, f"Resposta de: {c.frente}",
                                c.topico, "manual", c.criado_em))
        id_do_card = {
            (d, frente): i
            for i, d, frente in cur.execute(
                "SELECT f.id, d.nome, f.frente FROM flashcards f "
                "JOIN disciplinas d ON d.id = f.disciplina_id WHERE d.usuario_id = %s",
                (usuario_id,),
            )
        }

        # 2. histórico
        with cur.copy(
            "COPY historico_revisoes (flashcard_id, nota, facilidade_anterior, facilidade_nova, "
            "intervalo_anterior, intervalo_novo, repeticoes_anterior, repeticoes_nova, "
            "proxima_revisao_anterior, proxima_revisao_nova, revisado_em) FROM STDIN"
        ) as copy:
            for card, nota, ant, novo, prox_ant, prox_nova, quando in historico:
                copy.write_row((id_do_card[(card.disciplina, card.frente)], nota,
                                ant.facilidade, novo.facilidade, ant.intervalo, novo.intervalo,
                                ant.repeticoes, novo.repeticoes, prox_ant, prox_nova, quando))

        # 3. estado final: tabela temporária + um único UPDATE ... FROM
        cur.execute(
            """CREATE TEMP TABLE estado_final (
                   flashcard_id bigint PRIMARY KEY, facilidade numeric(4,2), intervalo_dias int,
                   repeticoes int, proxima_revisao timestamptz, ultima_revisao_em timestamptz,
                   versao int
               ) ON COMMIT DROP"""
        )
        with cur.copy("COPY estado_final FROM STDIN") as copy:
            for c in cards:
                copy.write_row((id_do_card[(c.disciplina, c.frente)], c.estado.facilidade,
                                c.estado.intervalo, c.estado.repeticoes, c.proxima, c.ultima,
                                c.versao))
        cur.execute(
            """UPDATE revisoes AS r
               SET facilidade = e.facilidade, intervalo_dias = e.intervalo_dias,
                   repeticoes = e.repeticoes, proxima_revisao = e.proxima_revisao,
                   ultima_revisao_em = e.ultima_revisao_em, versao = e.versao
               FROM estado_final AS e
               WHERE e.flashcard_id = r.flashcard_id"""
        )

        # 4. questões, alternativas e tentativas
        alternativa_id: dict[tuple[str, int], int] = {}
        for q in questoes:
            questao_id = cur.execute(
                """INSERT INTO questoes (disciplina_id, enunciado, tipo, explicacao, dificuldade,
                                         topico, origem)
                   VALUES (%s, %s, 'multipla_escolha', %s, %s, %s, 'manual') RETURNING id""",
                (disciplina_id[q.disciplina], q.enunciado, f"Explicação de: {q.enunciado}",
                 q.dificuldade, q.topico),
            ).fetchone()[0]
            for i in range(4):
                alternativa_id[(q.enunciado, i)] = cur.execute(
                    "INSERT INTO alternativas (questao_id, letra, texto, correta) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (questao_id, "ABCD"[i], f"Alternativa {'ABCD'[i]} de {q.enunciado}",
                     i == q.correta),
                ).fetchone()[0]
            q.id = questao_id  # type: ignore[attr-defined]
        with cur.copy(
            "COPY tentativas (questao_id, alternativa_id, correta, tempo_ms, respondida_em) FROM STDIN"
        ) as copy:
            for q, escolhida, quando, tempo in tentativas:
                copy.write_row((q.id, alternativa_id[(q.enunciado, escolhida)],
                                escolhida == q.correta, tempo, quando))
        conn.commit()  # o constraint trigger adiado confere as alternativas aqui

    with psycopg.connect(url_psycopg(), autocommit=True) as conn:
        for tabela in ("flashcards", "revisoes", "historico_revisoes", "tentativas"):
            conn.execute(f"ANALYZE {tabela}")
        vencidos = conn.execute(
            """SELECT count(*) FROM revisoes r JOIN disciplinas d ON d.id = r.disciplina_id
               WHERE d.usuario_id = %s AND r.proxima_revisao < now()""",
            (usuario_id,),
        ).fetchone()[0]

    print(
        f"usuário {usuario_id} ({EMAIL}): {len(cards)} cards, {len(historico)} revisões, "
        f"{len(questoes)} questões, {len(tentativas)} tentativas em {dias} dias; "
        f"{vencidos} cards vencidos agora; {time.perf_counter() - t0:.1f}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dias", type=int, default=42)
    main(parser.parse_args().dias)
