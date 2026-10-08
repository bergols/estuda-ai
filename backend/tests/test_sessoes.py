"""Sincronização das sessões de estudo (fase 7): idempotência pela API.

O app desktop reenvia tudo o que não teve confirmação. Estes testes provam que o
reenvio não duplica nada, que o status nunca volta atrás, que uma sessão inválida
não trava o lote e que nada vaza entre usuários.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Disciplina
from app.schemas import LoteSessoes
from app.servicos import sessoes

INICIO = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def sessao(**campos) -> dict:
    padrao = {
        "chave": str(uuid.uuid4()),
        "metodo": "pomodoro",
        "foco_min": 25,
        "pausa_min": 5,
        "ciclos": 2,
        "sistema": "macos",
        "status": "em_andamento",
        "iniciada_em": _iso(INICIO),
        "pausas": [],
        "eventos": [],
    }
    return padrao | campos


def pausa(minuto: int, duracao_min: int = 5, **campos) -> dict:
    ini = INICIO + timedelta(minutes=minuto)
    return {"chave": str(uuid.uuid4()), "tipo": "curta", "iniciada_em": _iso(ini),
            "terminada_em": _iso(ini + timedelta(minutes=duracao_min))} | campos


def evento(tipo: str = "programa_bloqueado", **campos) -> dict:
    base = {"chave": str(uuid.uuid4()), "tipo": tipo, "ocorrido_em": _iso(INICIO + timedelta(minutes=3))}
    if tipo == "saida_janela":
        base["duracao_s"] = 40
    return base | campos


def enviar(client, headers, *itens):
    resposta = client.post("/sessoes/sincronizar", json={"sessoes": list(itens)}, headers=headers)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["sessoes"]


def contagens(session, chave: str):
    return session.execute(
        text("""SELECT count(*),
                       (SELECT count(*) FROM pausas_sessao p JOIN sessoes_estudo s ON s.id = p.sessao_id
                        WHERE s.chave = :c),
                       (SELECT count(*) FROM eventos_foco e JOIN sessoes_estudo s ON s.id = e.sessao_id
                        WHERE s.chave = :c)
                FROM sessoes_estudo WHERE chave = :c"""),
        {"c": chave},
    ).one()


def test_primeiro_envio_cria_sessao_pausas_e_eventos(client, headers, session):
    s = sessao(pausas=[pausa(25)], eventos=[evento(), evento("saida_janela")])
    [r] = enviar(client, headers, s)
    assert r["resultado"] == "criada"
    assert r["id"] > 0
    assert (r["pausas_novas"], r["eventos_novos"]) == (1, 2)
    assert tuple(contagens(session, s["chave"])) == (1, 1, 2)


def test_reenviar_o_mesmo_lote_nao_duplica_nada(client, headers, session):
    s = sessao(pausas=[pausa(25)], eventos=[evento()])
    [primeiro] = enviar(client, headers, s)
    for _ in range(3):  # timeout, Wi-Fi caiu, app reaberto...
        [r] = enviar(client, headers, s)
        assert r == primeiro | {"resultado": "sem_mudanca", "pausas_novas": 0, "eventos_novos": 0}
    assert tuple(contagens(session, s["chave"])) == (1, 1, 1)


def test_reenvio_com_pausa_nova_grava_so_a_nova(client, headers, session):
    p1 = pausa(25)
    s = sessao(pausas=[p1])
    enviar(client, headers, s)
    [r] = enviar(client, headers, s | {"pausas": [p1, pausa(55)]})
    assert (r["resultado"], r["pausas_novas"]) == ("sem_mudanca", 1)
    assert contagens(session, s["chave"])[1] == 2


def test_status_avanca_e_nunca_volta(client, headers, session):
    s = sessao()
    fim = _iso(INICIO + timedelta(minutes=60))
    enviar(client, headers, s)
    [r] = enviar(client, headers, s | {"status": "concluida", "terminada_em": fim})
    assert r["resultado"] == "atualizada"
    # Um envio ANTIGO ("em andamento") chega depois, fora de ordem: é ignorado
    [r] = enviar(client, headers, s)
    assert r["resultado"] == "sem_mudanca"
    # E uma sessão concluída não vira abandonada
    [r] = enviar(client, headers, s | {"status": "abandonada", "terminada_em": fim})
    assert r["resultado"] == "sem_mudanca"
    linha = session.execute(
        text("SELECT status, duracao_real_s FROM sessoes_estudo WHERE chave = :c"), {"c": s["chave"]}
    ).one()
    assert tuple(linha) == ("concluida", 3600)


def test_sessao_invalida_e_recusada_sem_travar_o_lote(client, headers, session):
    boa = sessao()
    ruim = sessao(metodo="52_17", foco_min=50, pausa_min=17)  # 52/17 com 50 min
    sem_fim = sessao(status="concluida")  # concluída sem terminada_em
    r = enviar(client, headers, ruim, boa, sem_fim)
    assert [x["resultado"] for x in r] == ["recusada", "criada", "recusada"]
    assert r[0]["erro"] == "ck_sessoes_estudo_metodo_52_17"
    assert r[2]["erro"] == "ck_sessoes_estudo_fim_conforme_status"
    assert contagens(session, boa["chave"])[0] == 1
    assert contagens(session, ruim["chave"])[0] == 0


def test_evento_invalido_recusa_a_sessao_inteira(client, headers, session):
    # Evento de saída da janela sem duração: a sessão e as partes dela são desfeitas
    # juntas (SAVEPOINT), nada fica pela metade
    s = sessao(pausas=[pausa(25)], eventos=[evento("saida_janela", duracao_s=None)])
    [r] = enviar(client, headers, s)
    assert (r["resultado"], r["erro"]) == ("recusada", "ck_eventos_foco_duracao_so_na_saida")
    assert tuple(contagens(session, s["chave"])) == (0, 0, 0)


def test_data_no_futuro_e_recusada(client, headers):
    futuro = datetime.now(UTC) + timedelta(days=3)
    [r] = enviar(client, headers, sessao(iniciada_em=_iso(futuro)))
    assert r["resultado"] == "recusada"
    assert "relógio" in r["erro"]


def test_disciplina_propria_e_ligada(client, headers, disciplina_id, session):
    [r] = enviar(client, headers, sessao(disciplina_id=disciplina_id))
    assert r["disciplina_descartada"] is False
    gravada = session.scalar(text("SELECT disciplina_id FROM sessoes_estudo WHERE id = :i"), {"i": r["id"]})
    assert gravada == disciplina_id


def test_disciplina_de_outro_usuario_e_descartada(client, headers, session, outro_usuario):
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Da vítima")
    session.add(alheia)
    session.flush()
    [r] = enviar(client, headers, sessao(disciplina_id=alheia.id))
    # gravada sem a disciplina (o tempo estudado não se perde), e o app fica sabendo
    assert (r["resultado"], r["disciplina_descartada"]) == ("criada", True)
    assert session.scalar(text("SELECT disciplina_id FROM sessoes_estudo WHERE id = :i"), {"i": r["id"]}) is None


def test_mesma_chave_em_dois_usuarios_sao_sessoes_diferentes(client, headers, session, outro_usuario):
    from tests.conftest import cabecalho

    s = sessao()
    [meu] = enviar(client, headers, s)
    [dele] = enviar(client, cabecalho(outro_usuario), s | {"status": "abandonada",
                                                          "terminada_em": _iso(INICIO + timedelta(minutes=5))})
    assert dele["resultado"] == "criada"  # não "atualizou" a sessão do outro
    assert meu["id"] != dele["id"]
    assert session.scalar(text("SELECT status FROM sessoes_estudo WHERE id = :i"), {"i": meu["id"]}) == "em_andamento"


@pytest.mark.parametrize(
    "mudanca",
    [
        {"iniciada_em": "2026-10-08T13:00:00"},  # sem fuso: ambíguo
        {"metodo": "maratona"},
        {"foco_min": 0},
        {"chave": "não é uuid"},
    ],
)
def test_formato_invalido_da_422(client, headers, mudanca):
    resposta = client.post("/sessoes/sincronizar", json={"sessoes": [sessao(**mudanca)]}, headers=headers)
    assert resposta.status_code == 422


def test_lote_limitado_a_50_sessoes(client, headers):
    resposta = client.post("/sessoes/sincronizar", json={"sessoes": [sessao() for _ in range(51)]},
                           headers=headers)
    assert resposta.status_code == 422


def test_listar_traz_os_numeros_derivados(client, headers, disciplina_id):
    fim = _iso(INICIO + timedelta(minutes=60))
    enviar(client, headers, sessao(disciplina_id=disciplina_id, status="concluida", terminada_em=fim,
                                   meta="lista 3", pausas=[pausa(25, 10)],
                                   eventos=[evento("saida_janela", duracao_s=120), evento()]))
    [s] = client.get("/sessoes", headers=headers).json()
    assert s["disciplina"] is not None
    assert (s["duracao_real_s"], s["pausas_s"], s["fora_s"]) == (3600, 600, 120)
    assert s["foco_efetivo_s"] == 3600 - 600 - 120
    assert (s["interrupcoes"], s["meta"]) == (2, "lista 3")


# --------------------------------------------------------------- concorrência real


@pytest.fixture
def usuario_commitado(engine, engine_dono):
    with Session(engine) as s:
        usuario_id = s.execute(
            text("INSERT INTO usuarios (nome, email) VALUES ('Sincronia', :e) RETURNING id"),
            {"e": f"sync-{uuid.uuid4().hex[:8]}@x.com"},
        ).scalar_one()
        s.commit()
    yield usuario_id
    with Session(engine_dono) as s:
        s.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": usuario_id})
        s.commit()


def test_dois_envios_simultaneos_da_mesma_sessao_gravam_uma(engine, usuario_commitado):
    """O app manda a sessão; a resposta demora; o app (ou o outro computador) manda de
    novo ENQUANTO o primeiro envio ainda não deu COMMIT.

    O 1o envio grava e segura o COMMIT; o 2o começa nesse meio-tempo e fica esperando
    no índice único (o Postgres não sabe ainda se a linha do 1o vai existir). Quando o
    1o confirma, o 2o cai no ON CONFLICT."""
    lote = LoteSessoes.model_validate({"sessoes": [sessao(pausas=[pausa(25)])]})
    gravou = threading.Event()

    def primeiro():
        with Session(engine) as s:
            resultado = sessoes._gravar(s, usuario_commitado, lote.sessoes[0], datetime.now(UTC))
            gravou.set()
            threading.Event().wait(0.5)  # segura a transação aberta: o 2o vai esbarrar nela
            s.commit()
            return resultado.resultado

    def segundo():
        gravou.wait()
        with Session(engine) as s:
            [resultado] = sessoes.sincronizar(s, usuario_id=usuario_commitado, lote=lote)
            return resultado.resultado

    with ThreadPoolExecutor(2) as executor:
        f1, f2 = executor.submit(primeiro), executor.submit(segundo)
        resultados = (f1.result(), f2.result())

    assert resultados == ("criada", "sem_mudanca")
    with Session(engine) as s:
        sessoes_gravadas, pausas_gravadas = s.execute(
            text("""SELECT count(*), (SELECT count(*) FROM pausas_sessao p
                                      JOIN sessoes_estudo x ON x.id = p.sessao_id
                                      WHERE x.usuario_id = :u)
                    FROM sessoes_estudo WHERE usuario_id = :u"""),
            {"u": usuario_commitado},
        ).one()
    assert (sessoes_gravadas, pausas_gravadas) == (1, 1)
