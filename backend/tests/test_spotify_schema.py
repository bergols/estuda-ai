"""Regras das tabelas do Spotify (fase 7) que vivem no banco."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Disciplina, PreferenciasFoco, SpotifyConta, SpotifyPlaylist

URI = "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"
CIFRADO = bytes([1]) + os.urandom(40)  # versão 1 + nonce + texto + tag (formato da cifra)


def _deve_violar(session, objeto, constraint):
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.add(objeto)
            session.flush()
    assert erro.value.orig.diag.constraint_name == constraint


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Cálculo II")
    session.add(d)
    session.flush()
    return d


def playlist(usuario_id, **campos):
    return SpotifyPlaylist(**({"usuario_id": usuario_id, "alvo": "padrao", "uri": URI, "nome": "Lo-fi"} | campos))


def test_uma_padrao_por_usuario_mesmo_com_nulls(session, usuario):
    """UNIQUE comum: NULL é diferente de NULL, e duas 'padrao' (metodo e disciplina NULL)
    passariam. NULLS NOT DISTINCT faz o banco tratar os NULLs como iguais."""
    session.add(playlist(usuario.id))
    session.flush()
    _deve_violar(session, playlist(usuario.id, nome="Outra"),
                 "uq_spotify_playlists_usuario_id_alvo_metodo_disciplina_id")


def test_uma_por_metodo_e_por_disciplina(session, usuario, disciplina):
    session.add_all([
        playlist(usuario.id, alvo="metodo", metodo="pomodoro"),
        playlist(usuario.id, alvo="metodo", metodo="bloco"),
        playlist(usuario.id, alvo="disciplina", disciplina_id=disciplina.id),
        playlist(usuario.id, alvo="intervalo"),
    ])
    session.flush()
    _deve_violar(session, playlist(usuario.id, alvo="metodo", metodo="pomodoro"),
                 "uq_spotify_playlists_usuario_id_alvo_metodo_disciplina_id")


@pytest.mark.parametrize(
    ("campos", "constraint"),
    [
        ({"alvo": "metodo"}, "ck_spotify_playlists_metodo_so_no_alvo_metodo"),
        ({"metodo": "pomodoro"}, "ck_spotify_playlists_metodo_so_no_alvo_metodo"),
        ({"alvo": "disciplina"}, "ck_spotify_playlists_disciplina_so_no_alvo_disciplina"),
        ({"alvo": "metodo", "metodo": "maratona"}, "ck_spotify_playlists_metodo_valido"),
        ({"alvo": "festa"}, "ck_spotify_playlists_alvo_valido"),
        ({"uri": "https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS"}, "ck_spotify_playlists_uri_valida"),
        ({"uri": "spotify:track:37i9dQZF1DX8Uebhn9wzrS"}, "ck_spotify_playlists_uri_valida"),
        ({"nome": "  "}, "ck_spotify_playlists_nome_valido"),
    ],
)
def test_regras_da_playlist(session, usuario, campos, constraint):
    _deve_violar(session, playlist(usuario.id, **campos), constraint)


def test_playlist_nao_aponta_para_disciplina_de_outro(session, outro_usuario, disciplina):
    _deve_violar(session, playlist(outro_usuario.id, alvo="disciplina", disciplina_id=disciplina.id),
                 "fk_spotify_playlists_disciplina_id_disciplinas")


def test_apagar_disciplina_leva_a_playlist_dela(session, usuario, disciplina):
    session.add_all([playlist(usuario.id), playlist(usuario.id, alvo="disciplina", disciplina_id=disciplina.id)])
    session.flush()
    session.delete(disciplina)
    session.flush()
    assert session.scalars(select(SpotifyPlaylist.alvo).where(SpotifyPlaylist.usuario_id == usuario.id)).all() == ["padrao"]


@pytest.mark.parametrize(
    ("campos", "constraint"),
    [
        ({"refresh_token": b"curto"}, "ck_spotify_contas_refresh_cifrado"),
        ({"refresh_token": bytes([0]) + os.urandom(40)}, "ck_spotify_contas_refresh_cifrado"),  # versão 0
        ({"access_token": CIFRADO}, "ck_spotify_contas_access_com_validade"),  # sem validade
        ({"access_expira_em": datetime(2026, 10, 9, tzinfo=UTC)}, "ck_spotify_contas_access_com_validade"),
    ],
)
def test_regras_da_conta(session, usuario, campos, constraint):
    conta = SpotifyConta(**({"usuario_id": usuario.id, "spotify_id": "abc", "escopos": "x",
                             "refresh_token": CIFRADO} | campos))
    _deve_violar(session, conta, constraint)


def test_versao_da_chave_e_legivel_no_sql(session, usuario):
    """O 1o byte do cifrado é a versão da chave: dá para saber quantos faltam recifrar."""
    session.add(SpotifyConta(usuario_id=usuario.id, spotify_id="abc", escopos="x", refresh_token=CIFRADO))
    session.flush()
    assert session.scalar(text("SELECT get_byte(refresh_token, 0) FROM spotify_contas WHERE usuario_id = :u"),
                          {"u": usuario.id}) == 1


def test_preferencia_padrao_e_valores(session, usuario):
    session.add(PreferenciasFoco(usuario_id=usuario.id))
    session.flush()
    assert session.get(PreferenciasFoco, usuario.id).spotify_no_intervalo == "pausar"
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(text("UPDATE preferencias_foco SET spotify_no_intervalo = 'aumentar' WHERE usuario_id = :u"),
                            {"u": usuario.id})
