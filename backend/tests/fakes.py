"""Dublês para os testes não dependerem do modelo real (lento e ~470 MB).

O EmbedderFalso faz um "bag of words" com hashing: cada palavra (minúscula, sem
acento) soma 1 numa das 384 posições do vetor, que depois é normalizado. Textos
que compartilham palavras ficam próximos, então dá para testar ordenação por
distância de cosseno, filtro por disciplina e a fusão híbrida. NÃO captura
sinônimos; isso é verificado no teste com o modelo real (marcador "modelo").
"""

import hashlib
import math
import re
import unicodedata

from app.models import EMBEDDING_DIM

_PALAVRA = re.compile(r"\S+")


def _normalizar(palavra: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", palavra.lower())
    return "".join(c for c in sem_acento if c.isalnum())


class EmbedderFalso:
    def __init__(self, falhar_apos: int | None = None):
        self.chamadas = 0
        self.falhar_apos = falhar_apos

    def tokenizar(self, texto: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in _PALAVRA.finditer(texto)]

    def _vetor(self, texto: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        for palavra in map(_normalizar, texto.split()):
            if palavra:
                h = int(hashlib.md5(palavra.encode()).hexdigest(), 16)
                v[h % EMBEDDING_DIM] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_trechos(self, textos: list[str]) -> list[list[float]]:
        self.chamadas += 1
        if self.falhar_apos is not None and self.chamadas > self.falhar_apos:
            raise RuntimeError("falha simulada no modelo")
        return [self._vetor(t) for t in textos]

    def embed_consulta(self, texto: str) -> list[float]:
        return self._vetor(texto)

    def embed_simetrico(self, textos: list[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]


class AnthropicFalso:
    """Dublê do cliente da Anthropic: devolve respostas roteirizadas, em ordem.

    Cada item de `roteiro` é um dict/modelo (vira o JSON da resposta), uma string
    (texto cru, para simular JSON inválido) ou uma exceção (levantada na chamada).
    Guarda os parâmetros de cada chamada em `chamadas` para os testes inspecionarem.
    """

    def __init__(self, *roteiro, tokens_entrada: int = 1000, tokens_saida: int = 200):
        self.roteiro = list(roteiro)
        self.chamadas: list[dict] = []
        self.tokens_entrada = tokens_entrada
        self.tokens_saida = tokens_saida
        self.messages = self  # o código chama cliente.messages.create(...)

    def create(self, **kwargs):
        import json
        from types import SimpleNamespace

        from pydantic import BaseModel

        self.chamadas.append(kwargs)
        if not self.roteiro:
            raise AssertionError("o código chamou o LLM mais vezes que o roteiro previa")
        item = self.roteiro.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            texto = item.model_dump_json()
        elif isinstance(item, (dict, list)):
            texto = json.dumps(item, ensure_ascii=False)
        else:
            texto = item
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=texto)],
            usage=SimpleNamespace(input_tokens=self.tokens_entrada, output_tokens=self.tokens_saida),
            stop_reason="end_turn",
        )


class SpotifyFalso:
    """O Spotify simulado (servidor de tokens + Web API) num httpx.MockTransport.

    Como o de verdade, pode TROCAR o refresh token a cada renovação (rotacionar=True):
    o antigo deixa de valer e usá-lo dá 400 invalid_grant. É isso que torna perigosa
    a renovação simultânea sem lock. `atraso_renovacao` segura a resposta para forçar
    a sobreposição nos testes de concorrência. Seguro para várias threads.
    """

    VERIFIER_ESPERADO = "v" * 43

    def __init__(self, rotacionar: bool = True):
        import threading

        self.rotacionar = rotacionar
        self.atraso_renovacao = 0.0
        self.renovacoes = 0
        self.refresh_valido = "refresh-1"
        self.access_atual = "access-1"
        self.revogado = False
        self.responder_429: dict | None = None  # ex.: {"retry_after": 7, "reason": None}
        self.playlists = [
            {"uri": "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS", "name": "Lo-fi para estudar",
             "owner": {"display_name": "Spotify"}, "images": [{"url": "https://i.scdn.co/x"}]},
            {"uri": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "name": "Um álbum (fica de fora)"},
        ]
        self._trava = threading.Lock()
        self._n = 1

    @property
    def transport(self):
        import httpx

        return httpx.MockTransport(self._responder)

    def _responder(self, pedido):
        import json
        import time
        from urllib.parse import parse_qs

        import httpx

        if self.responder_429 is not None:
            corpo = {"error": {"status": 429, "message": "x", "reason": self.responder_429.get("reason")}}
            return httpx.Response(429, json=corpo,
                                  headers={"Retry-After": str(self.responder_429.get("retry_after", 5))})
        if pedido.url.host == "accounts.spotify.com" and pedido.url.path == "/api/token":
            dados = {k: v[0] for k, v in parse_qs(pedido.content.decode()).items()}
            if dados["grant_type"] == "authorization_code":
                if dados.get("code") != "codigo-bom" or dados.get("code_verifier") != self.VERIFIER_ESPERADO:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._emitir(novo_refresh=True))
            with self._trava:
                refresh_enviado = dados.get("refresh_token")
                valido = not self.revogado and refresh_enviado == self.refresh_valido
            time.sleep(self.atraso_renovacao)
            if not valido:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token revoked"})
            with self._trava:
                if refresh_enviado != self.refresh_valido:  # outro renovou durante o atraso
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.renovacoes += 1
                return httpx.Response(200, json=self._emitir(novo_refresh=self.rotacionar))
        if pedido.url.host == "api.spotify.com":
            if pedido.headers.get("authorization") != f"Bearer {self.access_atual}":
                return httpx.Response(401, json={"error": {"status": 401, "message": "expired"}})
            if pedido.url.path == "/v1/me":
                return httpx.Response(200, json={"id": "spotify-user", "display_name": "Bergola"})
            if pedido.url.path == "/v1/me/playlists":
                return httpx.Response(200, content=json.dumps({"items": self.playlists}))
        return httpx.Response(404)

    def _emitir(self, novo_refresh: bool) -> dict:
        self._n += 1
        self.access_atual = f"access-{self._n}"
        corpo = {"access_token": self.access_atual, "token_type": "Bearer", "expires_in": 3600,
                 "scope": "user-read-playback-state user-modify-playback-state"}
        if novo_refresh:
            self.refresh_valido = f"refresh-{self._n}"
            corpo["refresh_token"] = self.refresh_valido
        return corpo
