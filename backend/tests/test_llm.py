from decimal import Decimal

import anthropic
import pytest
from pydantic import BaseModel, Field

from app.servicos.llm import ClienteLLM, ErroGeracao, custo_usd, schema_para_api
from tests.fakes import AnthropicFalso


class Saida(BaseModel):
    resposta: str
    nota: int = Field(ge=1, le=5)


def cliente(*roteiro):
    falso = AnthropicFalso(*roteiro)
    return ClienteLLM("claude-haiku-4-5-20251001", cliente=falso), falso


def test_resposta_valida_na_primeira_chamada():
    llm, falso = cliente({"resposta": "ok", "nota": 3})

    r = llm.gerar("sistema", "pergunta", Saida)

    assert r.dados == Saida(resposta="ok", nota=3)
    assert r.uso.chamadas == 1 and r.uso.tokens_entrada == 1000
    formato = falso.chamadas[0]["output_config"]["format"]
    assert formato["type"] == "json_schema"
    assert formato["schema"]["additionalProperties"] is False
    assert "minimum" not in str(formato["schema"])  # restrição validada do nosso lado


def test_validacao_falha_tenta_de_novo_com_o_erro_e_soma_os_tokens():
    llm, falso = cliente({"resposta": "ok", "nota": 9}, {"resposta": "ok", "nota": 4})

    r = llm.gerar("sistema", "pergunta", Saida)

    assert r.dados.nota == 4
    assert r.uso.chamadas == 2
    assert r.uso.tokens_entrada == 2000  # a tentativa que falhou também foi cobrada
    segunda = falso.chamadas[1]["messages"]
    assert segunda[1]["role"] == "assistant"  # a resposta inválida volta ao modelo...
    assert "nota" in segunda[2]["content"]  # ...com o motivo da falha


def test_duas_respostas_invalidas_viram_erro_de_validacao():
    llm, _ = cliente("isto não é JSON", {"resposta": "ok", "nota": 0})

    with pytest.raises(ErroGeracao) as erro:
        llm.gerar("sistema", "pergunta", Saida)

    assert erro.value.status == "erro_validacao"
    assert erro.value.uso.chamadas == 2


def test_regra_de_negocio_tambem_conta_como_validacao():
    def validar(s: Saida):
        if s.resposta != "certo":
            raise ValueError("resposta precisa ser 'certo'")

    llm, _ = cliente({"resposta": "errado", "nota": 1}, {"resposta": "certo", "nota": 1})

    assert llm.gerar("s", "p", Saida, validar).dados.resposta == "certo"


def test_erro_da_api_vira_erro_api_sem_nova_tentativa():
    falha = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    llm, falso = cliente(falha)

    with pytest.raises(ErroGeracao) as erro:
        llm.gerar("s", "p", Saida)

    assert erro.value.status == "erro_api"
    assert len(falso.chamadas) == 1


def test_custo_usa_o_preco_do_modelo_e_aceita_id_com_data():
    assert custo_usd("claude-haiku-4-5-20251001", 1_000_000, 0) == Decimal("1.000000")
    assert custo_usd("claude-haiku-4-5", 4000, 600) == Decimal("0.007000")
    assert custo_usd("modelo-desconhecido", 10, 10) is None


def test_schema_aninhado_fecha_todos_os_objetos():
    class Item(BaseModel):
        x: int

    class Lista(BaseModel):
        itens: list[Item] = Field(min_length=1)

    schema = schema_para_api(Lista)
    assert schema["$defs"]["Item"]["additionalProperties"] is False
    assert "minItems" not in str(schema)
