"""Embeddings locais com sentence-transformers (intfloat/multilingual-e5-small).

Um embedding é um vetor de 384 números que representa o significado de um texto:
textos com sentido parecido viram vetores que apontam para direções parecidas.

Detalhes do e5 que importam:
- Ele foi treinado com prefixos: "passage: " para os documentos e "query: " para
  as perguntas. Sem eles a qualidade da busca cai.
- normalize_embeddings=True deixa todo vetor com comprimento 1. Aí distância de
  cosseno e produto interno dão a mesma ordenação (ver docs/busca-semantica.md).
"""

from functools import cached_property, lru_cache
from typing import Protocol

from app.config import get_settings
from app.models import EMBEDDING_DIM


class Embedder(Protocol):
    def tokenizar(self, texto: str) -> list[tuple[int, int]]: ...
    def embed_trechos(self, textos: list[str]) -> list[list[float]]: ...
    def embed_consulta(self, texto: str) -> list[float]: ...
    def embed_simetrico(self, textos: list[str]) -> list[list[float]]: ...


class EmbedderE5:
    def __init__(self, nome_modelo: str):
        self.nome_modelo = nome_modelo

    @cached_property
    def modelo(self):
        # Import e carga preguiçosos: só quem realmente gera embeddings paga os
        # ~2 s de carga (e o download de ~470 MB na primeira vez).
        from sentence_transformers import SentenceTransformer

        modelo = SentenceTransformer(self.nome_modelo, device="cpu")
        dimensao = modelo.get_embedding_dimension()
        if dimensao != EMBEDDING_DIM:
            # Falhar cedo: o banco recusaria cada INSERT com "expected 384 dimensions".
            raise RuntimeError(
                f"{self.nome_modelo} gera vetores de {dimensao} dimensões, mas a coluna "
                f"trechos.embedding é vector({EMBEDDING_DIM}). Crie uma migration."
            )
        return modelo

    def tokenizar(self, texto: str) -> list[tuple[int, int]]:
        codificado = self.modelo.tokenizer(
            texto, add_special_tokens=False, return_offsets_mapping=True, verbose=False
        )
        return [(i, f) for i, f in codificado["offset_mapping"] if f > i]

    def embed_trechos(self, textos: list[str]) -> list[list[float]]:
        vetores = self.modelo.encode(
            [f"passage: {t}" for t in textos], normalize_embeddings=True, batch_size=16
        )
        return vetores.tolist()

    def embed_consulta(self, texto: str) -> list[float]:
        return self.modelo.encode(f"query: {texto}", normalize_embeddings=True).tolist()

    def embed_simetrico(self, textos: list[str]) -> list[list[float]]:
        """Para comparar textos do mesmo tipo entre si (ex.: flashcard x flashcard).
        O e5 recomenda o prefixo "query: " dos dois lados nesse caso."""
        vetores = self.modelo.encode(
            [f"query: {t}" for t in textos], normalize_embeddings=True, batch_size=16
        )
        return vetores.tolist()


@lru_cache
def get_embedder() -> Embedder:
    """Dependência do FastAPI: uma instância por processo (o modelo ocupa ~500 MB)."""
    return EmbedderE5(get_settings().embedding_model)


if __name__ == "__main__":
    # `python -m app.servicos.embeddings` baixa e testa o modelo.
    e = get_embedder()
    print(len(e.embed_consulta("teste")), "dimensões")
