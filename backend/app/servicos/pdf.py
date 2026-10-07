"""Extração de texto de PDFs com PyMuPDF.

Licença: PyMuPDF é AGPL. Num repositório privado não há problema; se o projeto
virar um serviço público, troque por pypdf. A troca fica isolada neste módulo.
"""

import re
from pathlib import Path

import pymupdf


class ErroExtracao(Exception):
    pass


def _limpar(texto: str) -> str:
    # O Postgres não aceita o caractere NUL (\x00) em colunas text, e alguns PDFs
    # o trazem. Sem esta linha, o INSERT inteiro falharia.
    texto = texto.replace("\x00", "")
    # Palavra hifenizada na quebra de linha: "consul-\ntas" -> "consultas"
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def extrair_paginas(caminho: Path) -> list[str]:
    """Texto de cada página, na ordem (índice 0 = página 1)."""
    try:
        with pymupdf.open(caminho) as doc:
            paginas = [_limpar(pagina.get_text()) for pagina in doc]
    except (pymupdf.FileDataError, RuntimeError) as erro:
        raise ErroExtracao(f"não foi possível ler o PDF: {erro}") from erro
    if not any(paginas):
        raise ErroExtracao("o PDF não tem texto extraível (é uma imagem escaneada?)")
    return paginas
