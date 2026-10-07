"""Cliente do LLM (API da Anthropic) com saída estruturada e validação.

Fluxo de uma geração:
1. Monta o JSON Schema a partir de um modelo Pydantic e pede ao Claude uma
   resposta nesse formato (structured outputs: output_config.format). A API
   garante JSON sintaticamente válido e no formato do schema.
2. Valida com Pydantic + regras de negócio (ex.: "toda citação aponta para um
   trecho que foi enviado"). Essas regras o schema não consegue expressar.
3. Se a validação falhar, tenta UMA vez de novo, devolvendo ao modelo a resposta
   dele e o erro. Se falhar de novo, levanta ErroGeracao("erro_validacao").
4. Soma os tokens de todas as chamadas: a tentativa que falhou também é cobrada.

O schema enviado à API não aceita restrições numéricas e de tamanho (minimum,
maxLength, minItems...). Elas são removidas do schema e continuam valendo na
validação Pydantic do nosso lado.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from typing import Any, Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# US$ por milhão de tokens (entrada, saída). Tabela de preços de out/2026.
# O custo é gravado em geracoes.custo_usd no momento da chamada; mudar esta
# tabela não altera o histórico (ver docs/geracao-llm.md).
PRECOS_POR_MILHAO: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
}

# Restrições que o JSON Schema de structured outputs não aceita.
_CHAVES_NAO_SUPORTADAS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "default",
}


def custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int) -> Decimal | None:
    """Custo estimado; None se o modelo não está na tabela (custo desconhecido).

    Compara por prefixo para aceitar IDs com data (claude-haiku-4-5-20251001).
    """
    for prefixo, (entrada, saida) in PRECOS_POR_MILHAO.items():
        if modelo.startswith(prefixo):
            total = (tokens_entrada * entrada + tokens_saida * saida) / Decimal(1_000_000)
            return total.quantize(Decimal("0.000001"))
    return None


def schema_para_api(formato: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema do Pydantic adaptado às regras de structured outputs:
    additionalProperties: false em todo objeto e sem restrições não suportadas."""

    def limpar(no: Any) -> Any:
        if isinstance(no, dict):
            limpo = {k: limpar(v) for k, v in no.items() if k not in _CHAVES_NAO_SUPORTADAS}
            if limpo.get("type") == "object" or "properties" in limpo:
                limpo["additionalProperties"] = False
            return limpo
        if isinstance(no, list):
            return [limpar(v) for v in no]
        return no

    return limpar(formato.model_json_schema())


@dataclass
class Uso:
    tokens_entrada: int = 0
    tokens_saida: int = 0
    chamadas: int = 0

    def somar(self, usage: Any) -> None:
        self.tokens_entrada += usage.input_tokens
        self.tokens_saida += usage.output_tokens
        self.chamadas += 1


@dataclass
class ResultadoLLM(Generic[T]):
    dados: T
    modelo: str
    uso: Uso
    duracao_ms: int


@dataclass
class ErroGeracao(Exception):
    status: str  # 'erro_validacao' | 'erro_api'
    mensagem: str
    modelo: str
    uso: Uso = field(default_factory=Uso)
    duracao_ms: int = 0
    geracao: Any = None  # linha de auditoria gravada para a falha (app.models.Geracao)

    def __str__(self) -> str:
        return self.mensagem


class ClienteLLM:
    def __init__(self, modelo: str, max_tokens: int = 8000, cliente: Any = None):
        self.modelo = modelo
        self.max_tokens = max_tokens
        if cliente is not None:  # injeção nos testes: nenhum teste chama a API real
            self.__dict__["cliente"] = cliente

    @cached_property
    def cliente(self) -> anthropic.Anthropic:
        # A chave vem de ANTHROPIC_API_KEY (lida pelo SDK). O SDK já tenta de novo,
        # com backoff, erros de rede, 429 e 5xx (max_retries).
        return anthropic.Anthropic(max_retries=2, timeout=120.0)

    def gerar(
        self,
        sistema: str,
        mensagem: str,
        formato: type[T],
        validar: Callable[[T], None] | None = None,
    ) -> ResultadoLLM[T]:
        """Chama o modelo e devolve a saída validada, ou levanta ErroGeracao."""
        schema = schema_para_api(formato)
        mensagens: list[dict[str, Any]] = [{"role": "user", "content": mensagem}]
        uso = Uso()
        inicio = time.monotonic()

        def ms() -> int:
            return int((time.monotonic() - inicio) * 1000)

        erro = ""
        for _ in range(2):  # 1 tentativa + 1 nova tentativa se a validação falhar
            try:
                resposta = self.cliente.messages.create(
                    model=self.modelo,
                    max_tokens=self.max_tokens,
                    system=sistema,
                    messages=mensagens,
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                )
            except anthropic.AnthropicError as e:
                raise ErroGeracao("erro_api", f"{type(e).__name__}: {e}", self.modelo, uso, ms()) from e
            uso.somar(resposta.usage)

            if resposta.stop_reason == "refusal":
                raise ErroGeracao("erro_api", "o modelo recusou o pedido", self.modelo, uso, ms())
            texto = "".join(b.text for b in resposta.content if b.type == "text")
            try:
                if resposta.stop_reason == "max_tokens":
                    raise ValueError("resposta cortada: atingiu max_tokens")
                dados = formato.model_validate_json(texto)
                if validar:
                    validar(dados)
                return ResultadoLLM(dados, self.modelo, uso, ms())
            except ValueError as e:  # ValidationError do Pydantic é um ValueError
                erro = _resumir_erro(e)
                mensagens += [
                    {"role": "assistant", "content": texto or "(resposta vazia)"},
                    {
                        "role": "user",
                        "content": (
                            f"Sua resposta não passou na validação: {erro}\n"
                            "Responda de novo, seguindo exatamente o formato e as regras."
                        ),
                    },
                ]
        raise ErroGeracao(
            "erro_validacao", f"saída inválida após 2 tentativas: {erro}", self.modelo, uso, ms()
        )


def _resumir_erro(e: ValueError) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, d['loc']))}: {d['msg']}" for d in e.errors()[:5]
        )
    return str(e)[:500]
