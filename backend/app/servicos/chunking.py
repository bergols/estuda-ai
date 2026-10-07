"""Divisão do texto em trechos (chunks) com sobreposição.

Por que dividir: o modelo de embeddings lê no máximo 512 tokens. E um vetor
para um capítulo inteiro "dilui" o significado; trechos menores dão buscas
mais precisas e cabem no contexto do LLM na fase 3.

Por que sobrepor: uma ideia que cai na fronteira entre dois trechos apareceria
cortada nos dois. Com ~50 tokens repetidos, ela fica inteira em pelo menos um.

Como a página é descoberta: todas as páginas são concatenadas num texto só,
guardando o deslocamento (em caracteres) onde cada uma começa. O tokenizador
devolve, para cada token, o intervalo de caracteres que ele ocupa. Assim cada
trecho sabe em que caractere começa e termina, e portanto em que página.
"""

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

# Recebe um texto e devolve (início, fim) em caracteres de cada token.
Tokenizador = Callable[[str], list[tuple[int, int]]]

SEPARADOR_PAGINAS = "\n\n"


@dataclass(frozen=True)
class TrechoGerado:
    ordem: int
    conteudo: str
    pagina: int
    pagina_fim: int
    num_tokens: int


def dividir_em_trechos(
    paginas: list[str],
    tokenizar: Tokenizador,
    tamanho: int = 500,
    sobreposicao: int = 50,
) -> list[TrechoGerado]:
    if not 0 <= sobreposicao < tamanho:
        raise ValueError("a sobreposição precisa ser menor que o tamanho do trecho")

    inicios_paginas: list[int] = []
    partes: list[str] = []
    posicao = 0
    for texto in paginas:
        inicios_paginas.append(posicao)
        partes.append(texto)
        posicao += len(texto) + len(SEPARADOR_PAGINAS)
    texto = SEPARADOR_PAGINAS.join(partes)

    tokens = tokenizar(texto)
    trechos: list[TrechoGerado] = []
    passo = tamanho - sobreposicao
    for inicio in range(0, len(tokens), passo):
        janela = tokens[inicio : inicio + tamanho]
        primeiro_char, ultimo_char = janela[0][0], janela[-1][1]
        trechos.append(
            TrechoGerado(
                ordem=len(trechos),
                conteudo=texto[primeiro_char:ultimo_char],
                # bisect_right devolve quantas páginas começam até aquele caractere,
                # que é exatamente o número (1-based) da página que o contém.
                pagina=bisect_right(inicios_paginas, primeiro_char),
                pagina_fim=bisect_right(inicios_paginas, ultimo_char - 1),
                num_tokens=len(janela),
            )
        )
        if inicio + tamanho >= len(tokens):
            break  # a janela já chegou ao fim; outra só repetiria a sobreposição
    return trechos
