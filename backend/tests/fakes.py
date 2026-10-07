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
