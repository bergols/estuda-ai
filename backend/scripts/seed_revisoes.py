"""Simula semanas de estudo de um ou mais alunos, para o analytics da fase 5.

    docker compose exec backend python -m scripts.seed_revisoes [--dias 42] [--alunos 1]

Aluno 0 = estudante@estuda-ai.local (sempre com a mesma semente: os números da
documentação vêm dele). Com --alunos N, cria também aluno-001..aluno-(N-1)
@estuda-ai.local, com habilidades diferentes, para dar volume ao EXPLAIN ANALYZE.
Apaga antes os alunos de seeds anteriores (e, em cascata, tudo deles).

Cada aluno tem 4 disciplinas x 5 tópicos e, dia a dia, no fuso de São Paulo:
- faltas (mais no fim de semana): os vencidos acumulam ATRASO;
- cards novos entrando aos poucos, cada lote vindo de uma GERAÇÃO de IA simulada
  (tabela geracoes, com tokens e custo; algumas falham);
- até 80 revisões por dia com notas sorteadas pela dificuldade do tópico, pelo
  atraso, pela maturidade do card e pela habilidade do aluno, e o SM-2 da API;
- questões de múltipla escolha (criadas por uma geração no 1o dia) respondidas
  com um distrator "mais tentador";
- uma apostila (material com 4 trechos) por tópico. Cada card e cada questão fica
  ligado a um trecho do seu tópico; 20% dos cards também a um trecho de OUTRO
  material (relação N:N: é o que faz contagens "por material" inflarem se a
  consulta não tomar cuidado).

Carga em massa (tudo numa transação: se algo falhar, nada fica pela metade):
- COPY em vez de INSERT para tudo; os ids gerados são lidos de volta com um
  SELECT por aluno (COPY não tem RETURNING);
- ANALYZE a cada 25 alunos, DURANTE a carga: sem estatísticas novas o planejador
  usa Seq Scan nesses SELECTs e a carga fica quadrática (200 alunos: 64 s sem, 17 s com);
- COPY dos cards dispara o trigger que cria o estado de cada um;
- estado final: COPY para uma TABELA TEMPORÁRIA e um único UPDATE ... FROM;
- no fim, REFRESH da materialized view (sem CONCURRENTLY: ninguém está lendo, e o
  refresh comum é mais rápido que o que compara linha a linha).
"""

import argparse
import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.servicos.llm import custo_usd
from app.servicos.sm2 import EstadoSM2, calcular, proxima_revisao
from scripts.seed_experimento import url_psycopg

EMAIL = "estudante@estuda-ai.local"
FUSO = ZoneInfo("America/Sao_Paulo")
MODELO = "claude-haiku-4-5-20251001"

DISCIPLINAS = {
    "Banco de Dados": ["Índices", "Transações", "Normalização", "SQL", "Busca vetorial"],
    "Estruturas de Dados": ["Árvores", "Grafos", "Hashing", "Listas", "Ordenação"],
    "Redes de Computadores": ["TCP/IP", "Roteamento", "DNS", "HTTP", "Camada de enlace"],
    "Cálculo II": ["Integrais", "Séries", "Derivadas parciais", "Integrais múltiplas", "Polares"],
}
CARDS_POR_TOPICO = 24
QUESTOES_POR_TOPICO = 3
TRECHOS_POR_MATERIAL = 4
NOVOS_POR_DIA = 12
LIMITE_DIARIO = 80


@dataclass
class Geracao:
    tipo: str
    disciplina: str
    criado_em: datetime
    tokens_entrada: int
    tokens_saida: int
    status: str = "sucesso"
    chamadas: int = 1
    erro: str | None = None


@dataclass
class Card:
    disciplina: str
    topico: str
    frente: str
    dificuldade: float  # 0 = fácil, 1 = difícil (escondida do SM-2)
    trechos: list[tuple[str, str, int]]  # (disciplina, tópico, índice do trecho)
    criado_em: datetime | None = None
    geracao: Geracao | None = None
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
    trecho: tuple[str, str, int]
    criado_em: datetime | None = None
    geracao: Geracao | None = None


@dataclass
class Simulacao:
    cards: list[Card]
    historico: list[tuple]
    questoes: list[Questao]
    tentativas: list[tuple]
    geracoes: list[Geracao]


def momento_local(dia: date, hora: float) -> datetime:
    """Um horário de parede em São Paulo, devolvido como instante UTC."""
    h, m = int(hora), int((hora % 1) * 60)
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=FUSO).astimezone(UTC)


def sortear_nota(rng: random.Random, card: Card, agora: datetime, habilidade: float) -> int:
    atraso = max(0.0, (agora - card.proxima).total_seconds() / 86400)
    p_lembrar = (0.95 + habilidade - 0.6 * card.dificuldade - 0.01 * min(atraso, 30)
                 + 0.02 * min(card.estado.repeticoes, 5))
    p_lembrar = min(0.98, max(0.05, p_lembrar))
    if rng.random() < p_lembrar:
        facil = 1 - card.dificuldade
        return rng.choices([3, 4, 5], weights=[1.0, 1.5 + facil, 0.5 + 2 * facil])[0]
    return rng.choices([0, 1, 2], weights=[0.2, 0.4, 0.4])[0]


def nova_geracao(rng, geracoes, tipo, disciplina, quando, entrada, saida) -> Geracao:
    """Uma geração com sucesso, às vezes precedida de uma que falhou (o gasto conta)."""
    sorte = rng.random()
    if sorte < 0.06:  # validação falhou duas vezes: cobra as 2 chamadas
        geracoes.append(Geracao(tipo, disciplina, quando - timedelta(minutes=2),
                                2 * entrada, 2 * saida, "erro_validacao", 2,
                                "saída inválida após 2 tentativas"))
    elif sorte < 0.08:  # erro de rede antes de qualquer resposta: sem tokens
        geracoes.append(Geracao(tipo, disciplina, quando - timedelta(minutes=1), 0, 0,
                                "erro_api", 1, "APIConnectionError"))
    g = Geracao(tipo, disciplina, quando, entrada, saida)
    geracoes.append(g)
    return g


def simular(dias: int, rng: random.Random, habilidade: float) -> Simulacao:
    hoje_local = datetime.now(FUSO).date()
    inicio = hoje_local - timedelta(days=dias)

    dificuldade_topico = {
        (d, t): rng.uniform(0.1, 0.65) for d, topicos in DISCIPLINAS.items() for t in topicos
    }
    nao_introduzidos: dict[str, list[Card]] = {}
    for d, topicos in DISCIPLINAS.items():
        cards = []
        for t in topicos:
            for i in range(CARDS_POR_TOPICO):
                trechos = [(d, t, i % TRECHOS_POR_MATERIAL)]
                if rng.random() < 0.2:  # também cita a apostila de outro tópico (N:N)
                    outro = rng.choice([x for x in topicos if x != t])
                    trechos.append((d, outro, rng.randrange(TRECHOS_POR_MATERIAL)))
                dificuldade = min(0.95, max(0.0, dificuldade_topico[(d, t)] + rng.gauss(0, 0.12)))
                cards.append(Card(d, t, f"{t}: pergunta {i + 1}", dificuldade, trechos))
        rng.shuffle(cards)
        nao_introduzidos[d] = cards
    questoes = [
        Questao(d, t, f"{t}: questão {i + 1}", rng.randint(1, 5), rng.randrange(4),
                (d, t, i % TRECHOS_POR_MATERIAL))
        for d, topicos in DISCIPLINAS.items() for t in topicos for i in range(QUESTOES_POR_TOPICO)
    ]

    ativos: list[Card] = []
    historico: list[tuple] = []  # (card, nota, anterior, novo, prox_anterior, prox_nova, quando)
    tentativas: list[tuple] = []  # (questao, alternativa_escolhida, quando, tempo_ms)
    geracoes: list[Geracao] = []
    questoes_criadas = False

    for n in range(dias):
        dia = inicio + timedelta(days=n)
        fim_de_semana = dia.weekday() >= 5
        if rng.random() > (0.6 if fim_de_semana else 0.88):
            continue  # faltou: os vencidos acumulam atraso
        agora = momento_local(dia, rng.uniform(18.5, 22.5))

        if not questoes_criadas:  # uma geração de questões por disciplina no 1o dia
            for d in DISCIPLINAS:
                g = nova_geracao(rng, geracoes, "questoes", d, agora - timedelta(minutes=20),
                                 rng.randint(9_000, 14_000), rng.randint(2_500, 4_000))
                for q in questoes:
                    if q.disciplina == d:
                        q.criado_em, q.geracao = g.criado_em, g
            questoes_criadas = True

        for d in DISCIPLINAS:  # cards novos do dia, vindos de uma geração
            novos = [nao_introduzidos[d].pop()
                     for _ in range(min(NOVOS_POR_DIA, len(nao_introduzidos[d])))]
            if not novos:
                continue
            g = nova_geracao(rng, geracoes, "flashcards", d, agora - timedelta(minutes=10),
                             rng.randint(8_000, 15_000), rng.randint(1_200, 2_000))
            for card in novos:
                card.criado_em, card.proxima, card.geracao = agora, agora, g
                ativos.append(card)

        for _ in range(rng.randint(0, 3)):  # perguntas ao material
            nova_geracao(rng, geracoes, "pergunta", rng.choice(list(DISCIPLINAS)),
                         agora - timedelta(minutes=rng.randint(30, 90)),
                         rng.randint(2_500, 4_500), rng.randint(200, 600))

        fim_do_dia = momento_local(dia + timedelta(days=1), 0)
        vencidos = sorted((c for c in ativos if c.proxima < fim_do_dia), key=lambda c: c.proxima)
        for card in vencidos[:LIMITE_DIARIO]:
            agora += timedelta(seconds=rng.randint(15, 90))
            nota = sortear_nota(rng, card, agora, habilidade)
            novo = calcular(card.estado, nota)
            proxima_nova = proxima_revisao(agora, novo.intervalo)
            historico.append((card, nota, card.estado, novo, card.proxima, proxima_nova, agora))
            card.estado, card.proxima, card.ultima = novo, proxima_nova, agora
            card.versao += 1

        for questao in rng.sample(questoes, rng.randint(2, 6)):
            agora += timedelta(seconds=rng.randint(30, 180))
            p_acerto = min(0.98, 0.92 + habilidade - 0.1 * questao.dificuldade)
            if rng.random() < p_acerto:
                escolhida = questao.correta
            else:  # o 1o distrator é o mais tentador
                erradas = [i for i in range(4) if i != questao.correta]
                escolhida = rng.choices(erradas, weights=[0.6, 0.25, 0.15])[0]
            tentativas.append((questao, escolhida, agora, rng.randint(8_000, 90_000)))

    # Gerações com instantes distintos: o instante serve de chave para ler os ids de volta.
    for i, g in enumerate(sorted(geracoes, key=lambda g: g.criado_em)):
        g.criado_em += timedelta(microseconds=i)
    return Simulacao(ativos, historico, [q for q in questoes if q.criado_em], tentativas, geracoes)


def gravar(cur, email: str, nome: str, sim: Simulacao) -> int:
    usuario_id = cur.execute(
        "INSERT INTO usuarios (nome, email, fuso_horario) VALUES (%s, %s, 'America/Sao_Paulo') "
        "RETURNING id",
        (nome, email),
    ).fetchone()[0]
    disciplina_id = {
        d: cur.execute(
            "INSERT INTO disciplinas (usuario_id, nome) VALUES (%s, %s) RETURNING id",
            (usuario_id, d),
        ).fetchone()[0]
        for d in DISCIPLINAS
    }

    # Auditoria das gerações (lida de volta pelo instante, que é único por aluno)
    with cur.copy(
        "COPY geracoes (usuario_id, disciplina_id, tipo, modelo, tokens_entrada, tokens_saida, "
        "custo_usd, duracao_ms, status, chamadas, erro_mensagem, criado_em) FROM STDIN"
    ) as copy:
        for g in sim.geracoes:
            copy.write_row((usuario_id, disciplina_id[g.disciplina], g.tipo, MODELO,
                            g.tokens_entrada, g.tokens_saida,
                            custo_usd(MODELO, g.tokens_entrada, g.tokens_saida),
                            1_000 + g.tokens_saida * 4, g.status, g.chamadas, g.erro, g.criado_em))
    geracao_id = dict(cur.execute(
        "SELECT criado_em, id FROM geracoes WHERE usuario_id = %s", (usuario_id,)
    ).fetchall())

    # Apostilas: um material por tópico, com 4 trechos
    with cur.copy(
        "COPY materiais (disciplina_id, titulo, tipo, status, hash_sha256, tamanho_bytes, "
        "num_paginas, processado_em) FROM STDIN"
    ) as copy:
        for d, topicos in DISCIPLINAS.items():
            for t in topicos:
                titulo = f"Apostila de {t}"
                copy.write_row((disciplina_id[d], titulo, "texto", "concluido",
                                hashlib.sha256(f"{email}/{d}/{t}".encode()).hexdigest(),
                                4_000, TRECHOS_POR_MATERIAL, None))
    material = {
        (d, titulo.removeprefix("Apostila de ")): (mid, did)
        for mid, d, titulo, did in cur.execute(
            "SELECT m.id, d.nome, m.titulo, d.id FROM materiais m "
            "JOIN disciplinas d ON d.id = m.disciplina_id WHERE d.usuario_id = %s",
            (usuario_id,),
        )
    }
    with cur.copy(
        "COPY trechos (material_id, disciplina_id, ordem, conteudo, pagina, pagina_fim, num_tokens) "
        "FROM STDIN"
    ) as copy:
        for (d, t), (mid, did) in material.items():
            for i in range(TRECHOS_POR_MATERIAL):
                copy.write_row((mid, did, i, f"{t}, parte {i + 1}: conteúdo da apostila.",
                                i + 1, i + 1, 12))
    trecho_id = {
        (d, t.removeprefix("Apostila de "), ordem): tid
        for tid, d, t, ordem in cur.execute(
            "SELECT t.id, d.nome, m.titulo, t.ordem FROM trechos t "
            "JOIN materiais m ON m.id = t.material_id JOIN disciplinas d ON d.id = t.disciplina_id "
            "WHERE d.usuario_id = %s",
            (usuario_id,),
        )
    }

    # Cards (o trigger cria o estado de cada um) e suas origens N:N
    with cur.copy(
        "COPY flashcards (disciplina_id, frente, verso, topico, origem, geracao_id, criado_em) "
        "FROM STDIN"
    ) as copy:
        for c in sim.cards:
            copy.write_row((disciplina_id[c.disciplina], c.frente, f"Resposta de: {c.frente}",
                            c.topico, "ia", geracao_id[c.geracao.criado_em], c.criado_em))
    id_do_card = {
        (d, frente): i
        for i, d, frente in cur.execute(
            "SELECT f.id, d.nome, f.frente FROM flashcards f "
            "JOIN disciplinas d ON d.id = f.disciplina_id WHERE d.usuario_id = %s",
            (usuario_id,),
        )
    }
    with cur.copy("COPY flashcard_trechos (flashcard_id, trecho_id) FROM STDIN") as copy:
        for c in sim.cards:
            for t in c.trechos:
                copy.write_row((id_do_card[(c.disciplina, c.frente)], trecho_id[t]))

    with cur.copy(
        "COPY historico_revisoes (flashcard_id, nota, facilidade_anterior, facilidade_nova, "
        "intervalo_anterior, intervalo_novo, repeticoes_anterior, repeticoes_nova, "
        "proxima_revisao_anterior, proxima_revisao_nova, revisado_em) FROM STDIN"
    ) as copy:
        for card, nota, ant, novo, prox_ant, prox_nova, quando in sim.historico:
            copy.write_row((id_do_card[(card.disciplina, card.frente)], nota,
                            ant.facilidade, novo.facilidade, ant.intervalo, novo.intervalo,
                            ant.repeticoes, novo.repeticoes, prox_ant, prox_nova, quando))

    cur.execute("TRUNCATE estado_final")
    with cur.copy("COPY estado_final FROM STDIN") as copy:
        for c in sim.cards:
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

    # Questões, alternativas, origens e tentativas
    with cur.copy(
        "COPY questoes (disciplina_id, enunciado, tipo, explicacao, dificuldade, topico, origem, "
        "geracao_id, criado_em) FROM STDIN"
    ) as copy:
        for q in sim.questoes:
            copy.write_row((disciplina_id[q.disciplina], q.enunciado, "multipla_escolha",
                            f"Explicação de: {q.enunciado}", q.dificuldade, q.topico, "ia",
                            geracao_id[q.geracao.criado_em], q.criado_em))
    id_da_questao = {
        (d, e): i
        for i, d, e in cur.execute(
            "SELECT q.id, d.nome, q.enunciado FROM questoes q "
            "JOIN disciplinas d ON d.id = q.disciplina_id WHERE d.usuario_id = %s",
            (usuario_id,),
        )
    }
    with cur.copy("COPY alternativas (questao_id, letra, texto, correta) FROM STDIN") as copy:
        for q in sim.questoes:
            for i in range(4):
                copy.write_row((id_da_questao[(q.disciplina, q.enunciado)], "ABCD"[i],
                                f"Alternativa {'ABCD'[i]} de {q.enunciado}", i == q.correta))
    with cur.copy("COPY questao_trechos (questao_id, trecho_id) FROM STDIN") as copy:
        for q in sim.questoes:
            copy.write_row((id_da_questao[(q.disciplina, q.enunciado)], trecho_id[q.trecho]))
    alternativa_id = {
        (qid, letra): aid
        for aid, qid, letra in cur.execute(
            "SELECT a.id, a.questao_id, a.letra FROM alternativas a "
            "JOIN questoes q ON q.id = a.questao_id JOIN disciplinas d ON d.id = q.disciplina_id "
            "WHERE d.usuario_id = %s",
            (usuario_id,),
        )
    }
    with cur.copy(
        "COPY tentativas (questao_id, alternativa_id, correta, tempo_ms, respondida_em) FROM STDIN"
    ) as copy:
        for q, escolhida, quando, tempo in sim.tentativas:
            qid = id_da_questao[(q.disciplina, q.enunciado)]
            copy.write_row((qid, alternativa_id[(qid, "ABCD"[escolhida])],
                            escolhida == q.correta, tempo, quando))
    return usuario_id


def main(dias: int, alunos: int) -> None:
    t0 = time.perf_counter()
    totais = {"cards": 0, "revisoes": 0, "tentativas": 0, "geracoes": 0}
    with psycopg.connect(url_psycopg()) as conn:  # uma transação até o commit()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM usuarios WHERE email = %s OR email LIKE 'aluno-%%@estuda-ai.local'",
            (EMAIL,),
        )
        cur.execute(
            """CREATE TEMP TABLE estado_final (
                   flashcard_id bigint PRIMARY KEY, facilidade numeric(4,2), intervalo_dias int,
                   repeticoes int, proxima_revisao timestamptz, ultima_revisao_em timestamptz,
                   versao int
               ) ON COMMIT DROP"""
        )
        for n in range(alunos):
            rng = random.Random(2026 + n)
            habilidade = 0.0 if n == 0 else rng.gauss(0, 0.08)
            sim = simular(dias, rng, habilidade)
            email, nome = (EMAIL, "Estudante") if n == 0 else (
                f"aluno-{n:03d}@estuda-ai.local", f"Aluno {n:03d}")
            usuario_id = gravar(cur, email, nome, sim)
            if n == 0:
                estudante_id = usuario_id
            totais["cards"] += len(sim.cards)
            totais["revisoes"] += len(sim.historico)
            totais["tentativas"] += len(sim.tentativas)
            totais["geracoes"] += len(sim.geracoes)
            if n and n % 25 == 0:
                # Estatísticas atualizadas DURANTE a carga. Sem isto, o planejador
                # acha que as tabelas ainda estão quase vazias e lê os ids de volta
                # com Seq Scan: cada aluno fica mais caro que o anterior (O(n²)).
                # ANALYZE pode rodar dentro da transação e já enxerga as linhas
                # ainda não commitadas.
                for tabela in ("flashcards", "materiais", "trechos", "questoes",
                               "alternativas", "geracoes", "disciplinas"):
                    cur.execute(f"ANALYZE {tabela}")
            if n and n % 50 == 0:
                print(f"  {n} alunos gravados ({time.perf_counter() - t0:.0f}s)")
        conn.commit()  # o constraint trigger adiado confere as alternativas aqui

    with psycopg.connect(url_psycopg(), autocommit=True) as conn:
        tabelas = ("flashcards", "revisoes", "historico_revisoes", "tentativas", "geracoes",
                   "flashcard_trechos", "questao_trechos", "trechos", "materiais")
        for tabela in tabelas:
            conn.execute(f"ANALYZE {tabela}")
        t_mv = time.perf_counter()
        conn.execute("REFRESH MATERIALIZED VIEW mv_respostas_diarias")
        duracao = int((time.perf_counter() - t_mv) * 1000)
        conn.execute(
            "UPDATE atualizacoes_mv SET atualizado_em = now(), duracao_ms = %s "
            "WHERE nome = 'mv_respostas_diarias'",
            (duracao,),
        )
        conn.execute("ANALYZE mv_respostas_diarias")
        vencidos = conn.execute(
            """SELECT count(*) FROM revisoes r JOIN disciplinas d ON d.id = r.disciplina_id
               WHERE d.usuario_id = %s AND r.proxima_revisao < now()""",
            (estudante_id,),
        ).fetchone()[0]

    print(
        f"{alunos} aluno(s) em {dias} dias: {totais['cards']:,} cards, "
        f"{totais['revisoes']:,} revisões, {totais['tentativas']:,} tentativas, "
        f"{totais['geracoes']:,} gerações; estudante = usuário {estudante_id} "
        f"({vencidos} cards vencidos agora); refresh da MV em {duracao} ms; "
        f"total {time.perf_counter() - t0:.1f}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dias", type=int, default=42)
    parser.add_argument("--alunos", type=int, default=1)
    args = parser.parse_args()
    main(args.dias, args.alunos)
