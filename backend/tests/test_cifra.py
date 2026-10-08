"""Cifra dos tokens guardados no banco (AES-256-GCM, app/servicos/cifra.py)."""

import base64
import os

import pytest

from app.servicos.cifra import Cifra, ErroCifra


def chave() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def cifra() -> Cifra:
    return Cifra.de_texto(f"1:{chave()}")


def test_ida_e_volta(cifra):
    blob = cifra.cifrar("token-secreto", "spotify:42:refresh")
    assert b"token-secreto" not in blob  # o banco só vê bytes opacos
    assert blob[0] == 1  # versão da chave no 1o byte
    assert cifra.decifrar(blob, "spotify:42:refresh") == "token-secreto"


def test_nonce_novo_a_cada_cifragem(cifra):
    # Mesmo texto, mesma chave: blobs diferentes (nonce aleatório)
    assert cifra.cifrar("x", "c") != cifra.cifrar("x", "c")


def test_token_de_outro_usuario_nao_decifra(cifra):
    """AAD: copiar o token cifrado de uma linha para outra não funciona."""
    blob = cifra.cifrar("token-do-42", "spotify:42:refresh")
    with pytest.raises(ErroCifra):
        cifra.decifrar(blob, "spotify:7:refresh")
    with pytest.raises(ErroCifra):
        cifra.decifrar(blob, "spotify:42:access")


def test_byte_adulterado_e_detectado(cifra):
    blob = bytearray(cifra.cifrar("token", "c"))
    blob[-1] ^= 0x01
    with pytest.raises(ErroCifra):
        cifra.decifrar(bytes(blob), "c")


def test_rotacao_de_chave():
    antiga, nova = chave(), chave()
    velha = Cifra.de_texto(f"1:{antiga}")
    blob_antigo = velha.cifrar("token", "c")

    girada = Cifra.de_texto(f"2:{nova},1:{antiga}")  # a nova na frente
    assert girada.decifrar(blob_antigo, "c") == "token"  # a antiga ainda decifra
    assert girada.precisa_recifrar(blob_antigo)
    blob_novo = girada.cifrar("token", "c")
    assert blob_novo[0] == 2 and not girada.precisa_recifrar(blob_novo)

    # Depois de tirar a chave 1, o que ficou nela não abre mais (e o erro diz por quê)
    with pytest.raises(ErroCifra, match="chave 1"):
        Cifra.de_texto(f"2:{nova}").decifrar(blob_antigo, "c")


@pytest.mark.parametrize(
    "texto",
    ["", "sem-versao", "x:abc", "0:" + "A" * 44, "1:curta==", f"1:{'A' * 44},1:{'B' * 44}"],
)
def test_configuracao_invalida(texto):
    with pytest.raises(ErroCifra):
        Cifra.de_texto(texto)
