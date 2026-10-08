"""Conexão com o Spotify: troca do código (PKCE), tokens cifrados, renovação, erros e
preferências. O Spotify é o SpotifyFalso (tests/fakes.py): nenhuma chamada real."""

import base64
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.servicos import spotify
from app.servicos.cifra import Cifra
from app.servicos.spotify import ClienteSpotify
from tests.fakes import SpotifyFalso

CONECTAR = {"code": "codigo-bom", "code_verifier": SpotifyFalso.VERIFIER_ESPERADO,
            "redirect_uri": "http://127.0.0.1:43821/callback"}
URI = "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"
URI_2 = "spotify:playlist:37i9dQZF1DWZeKCadgRdKQ"


def conectar(client, headers):
    resposta = client.post("/spotify/conectar", json=CONECTAR, headers=headers)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def vencer_access(session, usuario):
    """Faz o access token guardado parecer vencido (o próximo pedido renova)."""
    session.execute(text("UPDATE spotify_contas SET access_expira_em = now() - interval '1 minute' "
                         "WHERE usuario_id = :u"), {"u": usuario.id})


# ------------------------------------------------------------------ conectar


def test_conectar_guarda_os_tokens_cifrados(client, headers, session, usuario):
    estado = conectar(client, headers)
    assert (estado["conectado"], estado["nome"], estado["spotify_id"]) == (True, "Bergola", "spotify-user")
    linha = session.execute(text("SELECT refresh_token, access_token FROM spotify_contas WHERE usuario_id = :u"),
                            {"u": usuario.id}).one()
    # No banco, só bytes opacos: nem o refresh nem o access aparecem em claro
    assert b"refresh-" not in bytes(linha.refresh_token)
    assert b"access-" not in bytes(linha.access_token)
    assert bytes(linha.refresh_token)[0] == 1  # versão da chave


def test_estado_nunca_devolve_token(client, headers):
    conectar(client, headers)
    corpo = client.get("/spotify", headers=headers).text
    assert "refresh-" not in corpo and "access-" not in corpo


def test_codigo_ou_verifier_errado(client, headers):
    resposta = client.post("/spotify/conectar", json=CONECTAR | {"code_verifier": "x" * 43}, headers=headers)
    assert resposta.status_code == 409  # o Spotify recusou (invalid_grant)


@pytest.mark.parametrize(
    "redirect",
    ["http://localhost:43821/callback", "https://evil.com/callback", "http://127.0.0.1:43821/outra",
     "http://127.0.0.1.evil.com:43821/callback"],
)
def test_redirect_so_loopback(client, headers, redirect):
    resposta = client.post("/spotify/conectar", json=CONECTAR | {"redirect_uri": redirect}, headers=headers)
    assert resposta.status_code == 422


@pytest.mark.parametrize("verifier", ["curto", "v" * 129, "v" * 42 + "!"])
def test_verifier_fora_da_rfc_7636(client, headers, verifier):
    resposta = client.post("/spotify/conectar", json=CONECTAR | {"code_verifier": verifier}, headers=headers)
    assert resposta.status_code == 422


def test_sem_configuracao_responde_503(client, headers, settings_teste):
    settings_teste.spotify_client_id = None
    assert client.get("/spotify/config", headers=headers).status_code == 503
    settings_teste.spotify_client_id = "x"
    settings_teste.cifra_chaves = None
    assert client.post("/spotify/conectar", json=CONECTAR, headers=headers).status_code == 503


def test_config_traz_client_id_e_escopos(client, headers):
    corpo = client.get("/spotify/config", headers=headers).json()
    assert corpo["client_id"] == "client-id-de-teste"
    assert "user-modify-playback-state" in corpo["escopos"]


# ------------------------------------------------------------------- tokens


def test_token_valido_vem_do_banco_sem_chamar_o_spotify(client, headers, spotify_falso):
    conectar(client, headers)
    token = client.post("/spotify/token", json={}, headers=headers).json()
    assert token["access_token"] == spotify_falso.access_atual
    assert spotify_falso.renovacoes == 0


def test_token_vencido_e_renovado_e_o_refresh_novo_guardado(client, headers, session, usuario, spotify_falso):
    conectar(client, headers)
    vencer_access(session, usuario)
    antes = spotify_falso.refresh_valido
    token = client.post("/spotify/token", json={}, headers=headers).json()
    assert spotify_falso.renovacoes == 1
    assert token["access_token"] == spotify_falso.access_atual
    # O Spotify trocou o refresh token: o NOVO precisa estar guardado (o antigo morreu)
    assert spotify_falso.refresh_valido != antes
    vencer_access(session, usuario)
    assert client.post("/spotify/token", json={}, headers=headers).status_code == 200
    assert spotify_falso.renovacoes == 2


def test_spotify_que_nao_troca_o_refresh(client, headers, session, usuario, spotify_falso):
    spotify_falso.rotacionar = False
    conectar(client, headers)
    for _ in range(2):
        vencer_access(session, usuario)
        assert client.post("/spotify/token", json={}, headers=headers).status_code == 200
    assert spotify_falso.renovacoes == 2


def test_forcar_renova_mesmo_com_token_valido(client, headers, spotify_falso):
    """O app usa forcar quando o Spotify recusou (401) o token guardado antes da hora."""
    conectar(client, headers)
    client.post("/spotify/token", json={"forcar": True}, headers=headers)
    assert spotify_falso.renovacoes == 1


def test_autorizacao_revogada_desconecta(client, headers, session, usuario, spotify_falso):
    conectar(client, headers)
    vencer_access(session, usuario)
    spotify_falso.revogado = True  # o usuário tirou o acesso em spotify.com/account/apps
    resposta = client.post("/spotify/token", json={}, headers=headers)
    assert resposta.status_code == 409
    assert client.get("/spotify", headers=headers).json()["conectado"] is False


def test_sem_conexao_da_404(client, headers):
    assert client.post("/spotify/token", json={}, headers=headers).status_code == 404


def test_limite_de_taxa_repassa_o_retry_after(client, headers, session, usuario, spotify_falso):
    conectar(client, headers)
    vencer_access(session, usuario)
    spotify_falso.responder_429 = {"retry_after": 7, "reason": None}
    resposta = client.post("/spotify/token", json={}, headers=headers)
    assert (resposta.status_code, resposta.headers["retry-after"]) == (429, "7")
    assert "espere" in resposta.json()["detail"]
    assert client.get("/spotify", headers=headers).json()["conectado"] is True  # 429 não desconecta


def test_cota_esgotada_tem_mensagem_propria(client, headers, session, usuario, spotify_falso):
    conectar(client, headers)
    vencer_access(session, usuario)
    spotify_falso.responder_429 = {"retry_after": 3600, "reason": "QUOTA_EXCEEDED"}
    resposta = client.post("/spotify/token", json={}, headers=headers)
    assert resposta.status_code == 429
    assert "cota" in resposta.json()["detail"]


def test_rotacao_da_chave_de_cifra_no_uso(client, headers, session, usuario, settings_teste):
    """Chave nova na frente de CIFRA_CHAVES: a antiga ainda decifra, e a renovação já
    grava com a nova (1o byte = 2)."""
    conectar(client, headers)
    antiga = settings_teste.cifra_chaves.get_secret_value()
    nova = base64.b64encode(b"\x07" * 32).decode()
    from pydantic import SecretStr

    settings_teste.cifra_chaves = SecretStr(f"2:{nova},{antiga}")
    vencer_access(session, usuario)
    assert client.post("/spotify/token", json={}, headers=headers).status_code == 200
    versao = session.scalar(text("SELECT get_byte(refresh_token, 0) FROM spotify_contas WHERE usuario_id = :u"),
                            {"u": usuario.id})
    assert versao == 2


def test_desconectar(client, headers):
    conectar(client, headers)
    assert client.delete("/spotify", headers=headers).status_code == 204
    assert client.get("/spotify", headers=headers).json()["conectado"] is False


# --------------------------------------------------------------- playlists


def test_minhas_playlists_so_playlists(client, headers):
    conectar(client, headers)
    lista = client.get("/spotify/minhas-playlists", headers=headers).json()
    assert lista == [{"uri": URI, "nome": "Lo-fi para estudar", "dono": "Spotify", "imagem": "https://i.scdn.co/x"}]


def test_access_recusado_pelo_spotify_vira_409(client, headers, spotify_falso):
    conectar(client, headers)
    spotify_falso.access_atual = "outro"  # o Spotify invalidou o access antes da hora
    assert client.get("/spotify/minhas-playlists", headers=headers).status_code == 409


def test_preferencias_trocam_tudo_de_uma_vez(client, headers, disciplina_id):
    corpo = {"no_intervalo": "trocar", "playlists": [
        {"alvo": "padrao", "uri": URI, "nome": "Lo-fi"},
        {"alvo": "metodo", "metodo": "52_17", "uri": URI_2, "nome": "Foco profundo"},
        {"alvo": "disciplina", "disciplina_id": disciplina_id, "uri": URI_2, "nome": "Cálculo"},
        {"alvo": "intervalo", "uri": URI, "nome": "Pausa"},
    ]}
    estado = client.put("/spotify/preferencias", json=corpo, headers=headers).json()
    assert estado["no_intervalo"] == "trocar"
    assert len(estado["playlists"]) == 4
    # Salvar de novo com uma só: as outras somem (substitui, não acumula)
    estado = client.put("/spotify/preferencias", json={"playlists": [corpo["playlists"][0]]}, headers=headers).json()
    assert (estado["no_intervalo"], len(estado["playlists"])) == ("pausar", 1)


def test_preferencias_com_alvo_repetido_nao_mudam_nada(client, headers):
    client.put("/spotify/preferencias", json={"no_intervalo": "continuar",
                                              "playlists": [{"alvo": "padrao", "uri": URI, "nome": "A"}]},
               headers=headers)
    repetido = {"no_intervalo": "trocar", "playlists": [{"alvo": "padrao", "uri": URI, "nome": "A"},
                                                         {"alvo": "padrao", "uri": URI_2, "nome": "B"}]}
    resposta = client.put("/spotify/preferencias", json=repetido, headers=headers)
    assert resposta.status_code == 422
    estado = client.get("/spotify", headers=headers).json()
    # A transação inteira foi desfeita: nem a preferência nem o DELETE ficaram
    assert (estado["no_intervalo"], [p["nome"] for p in estado["playlists"]]) == ("continuar", ["A"])


def test_uri_e_link_de_navegador_sao_diferentes(client, headers):
    corpo = {"playlists": [{"alvo": "padrao", "uri": "https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS",
                            "nome": "A"}]}
    assert client.put("/spotify/preferencias", json=corpo, headers=headers).status_code == 422


# ----------------------------------------------- renovação simultânea (real)


@pytest.fixture
def conta_commitada(engine, engine_dono):
    """Usuário com o Spotify conectado e o access VENCIDO, commitado (conexões reais)."""
    cifra = Cifra.de_texto("1:" + base64.b64encode(bytes(32)).decode())
    with Session(engine) as s:
        u = s.execute(text("INSERT INTO usuarios (nome, email) VALUES ('Spotify', :e) RETURNING id"),
                      {"e": f"spot-{uuid.uuid4().hex[:8]}@x.com"}).scalar_one()
        s.execute(text("""INSERT INTO spotify_contas (usuario_id, spotify_id, escopos, refresh_token,
                                                      access_token, access_expira_em)
                          VALUES (:u, 'x', 'x', :r, :a, now() - interval '1 hour')"""),
                  {"u": u, "r": cifra.cifrar("refresh-1", f"spotify:{u}:refresh"),
                   "a": cifra.cifrar("access-velho", f"spotify:{u}:access")})
        s.commit()
    yield u, cifra
    with Session(engine_dono) as s:
        s.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": u})
        s.commit()


def test_dois_computadores_renovando_ao_mesmo_tempo(engine, conta_commitada):
    """O Mac e o Windows pedem token no mesmo instante, com o access vencido.

    Sem o FOR UPDATE, os dois mandariam o MESMO refresh token ao Spotify, que troca o
    refresh a cada renovação: um receberia o novo e o outro levaria invalid_grant (e a
    conta seria desconectada à toa). Com o lock, o segundo espera, relê a linha, vê o
    access novo e nem chama o Spotify."""
    usuario_id, cifra = conta_commitada
    falso = SpotifyFalso()
    falso.atraso_renovacao = 0.4  # a 1a renovação demora: a 2a chega no meio dela
    cliente = ClienteSpotify("x", httpx.Client(transport=falso.transport))
    largada = threading.Barrier(2)

    def pedir():
        largada.wait()
        with Session(engine) as s:
            return spotify.token_valido(s, cliente, cifra, usuario_id=usuario_id)[0]

    with ThreadPoolExecutor(2) as executor:
        tokens = list(executor.map(lambda _: pedir(), range(2)))

    assert falso.renovacoes == 1
    assert tokens[0] == tokens[1] == falso.access_atual
    with Session(engine) as s:
        guardado = s.scalar(text("SELECT refresh_token FROM spotify_contas WHERE usuario_id = :u"),
                            {"u": usuario_id})
    assert cifra.decifrar(guardado, f"spotify:{usuario_id}:refresh") == falso.refresh_valido


def test_margem_renova_antes_de_vencer(session, usuario, spotify_falso):
    """Token que vence em 1 min já é renovado (MARGEM de 2 min): não vale a pena
    entregar um token que pode morrer no meio da chamada."""
    cifra = Cifra.de_texto("1:" + base64.b64encode(bytes(32)).decode())
    session.execute(text("""INSERT INTO spotify_contas (usuario_id, spotify_id, escopos, refresh_token,
                                                        access_token, access_expira_em)
                            VALUES (:u, 'x', 'x', :r, :a, :e)"""),
                    {"u": usuario.id, "r": cifra.cifrar("refresh-1", f"spotify:{usuario.id}:refresh"),
                     "a": cifra.cifrar("access-velho", f"spotify:{usuario.id}:access"),
                     "e": datetime.now(UTC) + timedelta(minutes=1)})
    cliente = ClienteSpotify("x", httpx.Client(transport=spotify_falso.transport))
    access, _ = spotify.token_valido(session, cliente, cifra, usuario_id=usuario.id)
    assert access != "access-velho" and spotify_falso.renovacoes == 1
