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
class _Palavra:
    inicio: int  # caractere
    fim: int
    num_tokens: int


def _agrupar_em_palavras(tokens: list[tuple[int, int]]) -> list[_Palavra]:
    """O tokenizador do e5 quebra palavras em pedaços ("Índices" -> "▁Í" + "ndices").
    Um pedaço que continua uma palavra começa exatamente onde o anterior termina
    (sem espaço entre eles). Juntamos os pedaços para nunca cortar uma palavra."""
    palavras: list[_Palavra] = []
    for inicio, fim in tokens:
        if palavras and inicio == palavras[-1].fim:
            p = palavras[-1]
            palavras[-1] = _Palavra(p.inicio, fim, p.num_tokens + 1)
        else:
            palavras.append(_Palavra(inicio, fim, 1))
    return palavras


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

    palavras = _agrupar_em_palavras(tokenizar(texto))
    trechos: list[TrechoGerado] = []
    i = 0
    while i < len(palavras):
        # Enche o trecho com palavras inteiras até o limite de tokens.
        j, num_tokens = i, 0
        while j < len(palavras) and num_tokens + palavras[j].num_tokens <= tamanho:
            num_tokens += palavras[j].num_tokens
            j += 1
        if j == i:  # uma "palavra" maior que o trecho inteiro (ex.: URL enorme)
            j, num_tokens = i + 1, palavras[i].num_tokens

        primeiro_char, ultimo_char = palavras[i].inicio, palavras[j - 1].fim
        trechos.append(
            TrechoGerado(
                ordem=len(trechos),
                conteudo=texto[primeiro_char:ultimo_char],
                # bisect_right devolve quantas páginas começam até aquele caractere,
                # que é exatamente o número (1-based) da página que o contém.
                pagina=bisect_right(inicios_paginas, primeiro_char),
                pagina_fim=bisect_right(inicios_paginas, ultimo_char - 1),
                num_tokens=num_tokens,
            )
        )
        if j >= len(palavras):
            break
        # Sobreposição: o próximo trecho recomeça voltando palavras até somar
        # ~`sobreposicao` tokens (sempre avançando pelo menos uma palavra).
        k, repetidos = j, 0
        while k > i + 1 and repetidos < sobreposicao:
            k -= 1
            repetidos += palavras[k].num_tokens
        i = k
    return trechos
