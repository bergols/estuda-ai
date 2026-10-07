import pymupdf
import pytest

from app.servicos.chunking import dividir_em_trechos
from app.servicos.pdf import ErroExtracao, extrair_paginas
from tests.fakes import EmbedderFalso

tokenizar = EmbedderFalso().tokenizar


def palavras(prefixo: str, n: int) -> str:
    return " ".join(f"{prefixo}{i}" for i in range(n))


def test_trechos_tem_tamanho_e_sobreposicao_pedidos():
    trechos = dividir_em_trechos([palavras("p", 25)], tokenizar, tamanho=10, sobreposicao=3)

    # passo = 10 - 3 = 7: janelas começam nos tokens 0, 7, 14 e a de 21 cobre o resto
    assert [t.num_tokens for t in trechos] == [10, 10, 10, 4]
    assert [t.ordem for t in trechos] == [0, 1, 2, 3]
    # os 3 últimos tokens de um trecho são os 3 primeiros do próximo
    assert trechos[0].conteudo.split()[-3:] == trechos[1].conteudo.split()[:3]


def test_texto_menor_que_um_trecho_vira_um_trecho_so():
    trechos = dividir_em_trechos(["poucas palavras aqui"], tokenizar, tamanho=10, sobreposicao=3)

    assert len(trechos) == 1
    assert trechos[0].conteudo == "poucas palavras aqui"


def test_trecho_guarda_pagina_de_inicio_e_de_fim():
    paginas = [palavras("a", 6), palavras("b", 6), palavras("c", 6)]

    trechos = dividir_em_trechos(paginas, tokenizar, tamanho=8, sobreposicao=2)

    assert (trechos[0].pagina, trechos[0].pagina_fim) == (1, 2)  # a0..a5 + b0 b1
    assert (trechos[1].pagina, trechos[1].pagina_fim) == (2, 3)  # b0..b5 + c0 c1
    assert (trechos[2].pagina, trechos[2].pagina_fim) == (3, 3)


def test_sobreposicao_maior_que_tamanho_e_recusada():
    with pytest.raises(ValueError):
        dividir_em_trechos(["x"], tokenizar, tamanho=5, sobreposicao=5)


def test_extrai_texto_por_pagina(tmp_path):
    doc = pymupdf.open()
    for texto in ["Índices B-tree", "Busca vetorial"]:
        doc.new_page().insert_text((72, 72), texto)
    caminho = tmp_path / "a.pdf"
    doc.save(caminho)

    assert extrair_paginas(caminho) == ["Índices B-tree", "Busca vetorial"]


def test_pdf_sem_texto_gera_erro(tmp_path):
    doc = pymupdf.open()
    doc.new_page()  # página em branco, como um PDF escaneado sem OCR
    caminho = tmp_path / "vazio.pdf"
    doc.save(caminho)

    with pytest.raises(ErroExtracao, match="texto extraível"):
        extrair_paginas(caminho)
