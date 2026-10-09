"""Bloqueios do modo foco: validação, troca da lista (CTE), regras do banco e
concorrência entre dois computadores salvando ao mesmo tempo."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProgramaBloqueado, SiteBloqueado
from app.schemas import Bloqueios
from app.servicos import bloqueios


def test_padroes_antes_de_salvar(client, headers):
    r = client.get("/bloqueios", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "bloquear_sites": False, "bloquear_programas": False, "espera_emergencia_s": 60,
        "sites": [], "programas": {"macos": [], "windows": []},
    }


def test_salva_e_normaliza_o_que_se_cola_do_navegador(client, headers):
    r = client.put("/bloqueios", headers=headers, json={
        "bloquear_sites": True, "espera_emergencia_s": 120,
        "sites": ["https://www.YouTube.com/watch?v=x", "youtube.com", "m.facebook.com:443/"],
        "programas": {"macos": ["Discord", "discord", "Steam"], "windows": ["Discord.exe"]},
    })
    assert r.status_code == 200, r.text
    assert r.json() == {
        "bloquear_sites": True, "bloquear_programas": False, "espera_emergencia_s": 120,
        "sites": ["m.facebook.com", "youtube.com"],  # repetido conta uma vez
        # "Discord" e "discord" são o mesmo programa: fica o primeiro digitado
        "programas": {"macos": ["Discord", "Steam"], "windows": ["Discord.exe"]},
    }


@pytest.mark.parametrize("dominio", [
    "localhost", "youtube", "192.168.0.1", "you tube.com", "a.com\n1.2.3.4 banco.com",
    "-x.com", "x-.com", "a" * 64 + ".com", "x.c0m",
])
def test_dominio_invalido_e_422(client, headers, dominio):
    r = client.put("/bloqueios", headers=headers, json={"sites": [dominio]})
    assert r.status_code == 422


@pytest.mark.parametrize("nome", ["/Applications/Discord.app", "C:\\x\\Discord.exe", "a\nb", "", "x" * 101])
def test_programa_invalido_e_422(client, headers, nome):
    r = client.put("/bloqueios", headers=headers, json={"programas": {"macos": [nome]}})
    assert r.status_code == 422


@pytest.mark.parametrize("espera", [9, 601])
def test_espera_fora_do_limite_e_422(client, headers, espera):
    assert client.put("/bloqueios", headers=headers, json={"espera_emergencia_s": espera}).status_code == 422


def test_trocar_a_lista_mantem_o_que_ficou(client, headers, session, usuario):
    client.put("/bloqueios", headers=headers, json={"sites": ["youtube.com", "x.com"]})
    antes = session.scalar(text(
        "SELECT id FROM sites_bloqueados WHERE usuario_id = :u AND dominio = 'youtube.com'"
    ), {"u": usuario.id})
    r = client.put("/bloqueios", headers=headers, json={"sites": ["youtube.com", "reddit.com"]})
    assert r.json()["sites"] == ["reddit.com", "youtube.com"]
    # A linha que ficou é a MESMA (não foi apagada e recriada)
    depois = session.scalar(text(
        "SELECT id FROM sites_bloqueados WHERE usuario_id = :u AND dominio = 'youtube.com'"
    ), {"u": usuario.id})
    assert antes == depois
    # Lista vazia apaga tudo
    r = client.put("/bloqueios", headers=headers, json={"sites": []})
    assert r.json()["sites"] == []


# ----------------------------------------------------------- regras do banco


def _deve_violar(session, objeto, constraint):
    # SAVEPOINT: o erro desfaz só o INSERT, e a transação do teste segue usável
    with pytest.raises(IntegrityError, match=constraint):
        with session.begin_nested():
            session.add(objeto)
            session.flush()


@pytest.mark.parametrize("dominio", ["YouTube.com", "a.com\n1.2.3.4 banco.com", "localhost", "x.com "])
def test_banco_recusa_dominio_invalido(session, usuario, dominio):
    """A última barreira: mesmo escrevendo direto no banco (sem a API)."""
    _deve_violar(session, SiteBloqueado(usuario_id=usuario.id, dominio=dominio),
                 "ck_sites_bloqueados_dominio_valido")


@pytest.mark.parametrize("nome", ["/bin/sh", "C:\\x.exe", "a\tb", " Discord", ""])
def test_banco_recusa_programa_invalido(session, usuario, nome):
    _deve_violar(session, ProgramaBloqueado(usuario_id=usuario.id, sistema="macos", nome=nome),
                 "ck_programas_bloqueados_nome_valido")


def test_banco_trata_maiusculas_como_o_mesmo_programa(session, usuario):
    session.add(ProgramaBloqueado(usuario_id=usuario.id, sistema="macos", nome="Discord"))
    session.flush()
    _deve_violar(session, ProgramaBloqueado(usuario_id=usuario.id, sistema="macos", nome="DISCORD"),
                 "uq_programas_bloqueados_usuario_id_sistema_nome")


def test_mesmo_nome_em_outro_sistema_pode(session, usuario):
    session.add(ProgramaBloqueado(usuario_id=usuario.id, sistema="macos", nome="Discord"))
    session.add(ProgramaBloqueado(usuario_id=usuario.id, sistema="windows", nome="Discord"))
    session.flush()


# ------------------------------------------------------------- concorrência


@pytest.fixture
def usuario_commitado(engine, engine_dono):
    with Session(engine) as s:
        usuario_id = s.execute(
            text("INSERT INTO usuarios (nome, email) VALUES ('Bloqueios', :e) RETURNING id"),
            {"e": f"bloq-{uuid.uuid4().hex[:8]}@x.com"},
        ).scalar_one()
        s.commit()
    yield usuario_id
    with Session(engine_dono) as s:
        s.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": usuario_id})
        s.commit()


def test_dois_computadores_salvando_ao_mesmo_tempo_nao_misturam_as_listas(engine, usuario_commitado):
    """O Mac salva [a, b] e segura o COMMIT; o Windows salva [c, d] nesse meio-tempo.

    Sem trava, o DELETE do Windows não enxergaria a e b (ainda não confirmados), e a
    lista final seria [a, b, c, d], que ninguém escolheu. Com o UPSERT da linha de
    preferências no começo, o Windows espera o Mac terminar; o comando seguinte dele
    (a CTE) tira um snapshot NOVO (READ COMMITTED: um por comando), já vê a e b, e os
    apaga. Vence o último a salvar, inteiro."""
    gravou = threading.Event()

    def mac():
        with Session(engine) as s:
            s.execute(bloqueios.SQL_PREFERENCIAS,
                      {"u": usuario_commitado, "sites": True, "programas": False, "espera": 60})
            s.execute(bloqueios.SQL_SITES, {"u": usuario_commitado, "dominios": ["a.com", "b.com"]})
            gravou.set()
            threading.Event().wait(0.5)  # transação aberta: o Windows vai esbarrar nela
            s.commit()

    def windows():
        gravou.wait()
        with Session(engine) as s:
            bloqueios.salvar(s, usuario_id=usuario_commitado, dados=Bloqueios(sites=["c.com", "d.com"]))

    with ThreadPoolExecutor(2) as executor:
        for f in [executor.submit(mac), executor.submit(windows)]:
            f.result()

    with Session(engine) as s:
        final = s.scalars(text(
            "SELECT dominio FROM sites_bloqueados WHERE usuario_id = :u ORDER BY dominio"
        ), {"u": usuario_commitado}).all()
    assert final == ["c.com", "d.com"]
