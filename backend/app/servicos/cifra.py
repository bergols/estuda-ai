"""Cifra de segredos guardados no banco (fase 7): os tokens do Spotify.

Por que cifrar na APLICAÇÃO (AES-256-GCM, chave no .env) e não com pgcrypto
(pgp_sym_encrypt dentro do SQL)? Com pgcrypto a chave viaja DENTRO do comando SQL:
aparece em log de consultas lentas (log_min_duration_statement), em
pg_stat_statements e em qualquer erro que ecoe o comando, e passa pelo servidor do
banco. Aqui o Postgres só vê bytes opacos: um dump do banco (backup, vazamento)
sozinho não abre nada, porque a chave mora em outro lugar (o .env da VM).

Formato de cada valor cifrado (bytea):

    [1 byte: versão da chave][12 bytes: nonce][texto cifrado + 16 bytes de tag]

- GCM é cifra AUTENTICADA: a tag detecta qualquer alteração dos bytes; decifrar algo
  adulterado falha em vez de devolver lixo.
- Nonce aleatório de 12 bytes por cifragem: nunca repetir nonce com a mesma chave é a
  regra de ouro do GCM.
- AAD (dados associados) = um "contexto" como "spotify:42:refresh": entra na tag sem
  ser cifrado. Copiar o token cifrado do usuário 42 para a linha do usuário 7 (ou do
  campo refresh para o access) faz a decifragem FALHAR.
- O 1o byte diz com qual chave foi cifrado: a rotação de chave é "nova chave na frente
  de CIFRA_CHAVES; o que estava na antiga é recifrado quando for lido". Dá até para
  contar no SQL quantos faltam: WHERE get_byte(refresh_token, 0) < 2.
"""

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TAMANHO_NONCE = 12


class ErroCifra(Exception):
    pass


@dataclass(frozen=True)
class Cifra:
    chaves: dict[int, bytes]
    atual: int

    @classmethod
    def de_texto(cls, texto: str) -> "Cifra":
        """ "2:<base64>,1:<base64>" -> Cifra (a primeira é a atual)."""
        chaves: dict[int, bytes] = {}
        ordem: list[int] = []
        for parte in texto.split(","):
            versao_txt, sep, b64 = parte.strip().partition(":")
            if not sep or not versao_txt.isdigit():
                raise ErroCifra("CIFRA_CHAVES: use 'versão:base64', ex.: 1:AbC...=")
            versao = int(versao_txt)
            if not 1 <= versao <= 255 or versao in chaves:
                raise ErroCifra("CIFRA_CHAVES: versões de 1 a 255, sem repetir")
            try:
                chave = base64.b64decode(b64, validate=True)
            except binascii.Error as erro:
                raise ErroCifra("CIFRA_CHAVES: base64 inválido") from erro
            if len(chave) != 32:
                raise ErroCifra("CIFRA_CHAVES: cada chave tem 32 bytes (AES-256)")
            chaves[versao] = chave
            ordem.append(versao)
        if not ordem:
            raise ErroCifra("CIFRA_CHAVES vazia")
        return cls(chaves=chaves, atual=ordem[0])

    def cifrar(self, texto: str, contexto: str) -> bytes:
        nonce = os.urandom(TAMANHO_NONCE)
        cifrado = AESGCM(self.chaves[self.atual]).encrypt(nonce, texto.encode(), contexto.encode())
        return bytes([self.atual]) + nonce + cifrado

    def decifrar(self, blob: bytes, contexto: str) -> str:
        if len(blob) < 1 + TAMANHO_NONCE + 16:
            raise ErroCifra("valor cifrado curto demais")
        versao, nonce, cifrado = blob[0], blob[1 : 1 + TAMANHO_NONCE], blob[1 + TAMANHO_NONCE :]
        chave = self.chaves.get(versao)
        if chave is None:
            raise ErroCifra(f"cifrado com a chave {versao}, que não está em CIFRA_CHAVES")
        try:
            return AESGCM(chave).decrypt(nonce, cifrado, contexto.encode()).decode()
        except InvalidTag as erro:
            # Chave errada, bytes adulterados ou contexto (usuário/campo) diferente
            raise ErroCifra("não foi possível decifrar (dado adulterado ou de outro contexto)") from erro

    def precisa_recifrar(self, blob: bytes) -> bool:
        return blob[0] != self.atual
