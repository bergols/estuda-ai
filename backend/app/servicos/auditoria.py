"""Registro de cada chamada ao LLM na tabela geracoes."""

from sqlalchemy.orm import Session

from app.models import Geracao
from app.servicos.llm import Uso, custo_usd


def registrar(
    session: Session,
    *,
    usuario_id: int,
    disciplina_id: int,
    tipo: str,
    modelo: str,
    uso: Uso,
    duracao_ms: int,
    status: str = "sucesso",
    erro: str | None = None,
) -> Geracao:
    """Adiciona a linha de auditoria à transação atual (sem COMMIT).

    Quem chama decide a transação:
    - sucesso: na MESMA transação dos flashcards/questões gerados. Ou os dois
      ficam gravados, ou nenhum (não existe "custo registrado de cards que
      não foram salvos", nem o contrário);
    - falha: numa transação que só tem a auditoria, porque não há conteúdo a
      salvar, mas o gasto aconteceu e precisa ficar registrado.
    """
    geracao = Geracao(
        usuario_id=usuario_id,
        disciplina_id=disciplina_id,
        tipo=tipo,
        modelo=modelo,
        tokens_entrada=uso.tokens_entrada,
        tokens_saida=uso.tokens_saida,
        custo_usd=custo_usd(modelo, uso.tokens_entrada, uso.tokens_saida),
        duracao_ms=duracao_ms,
        status=status,
        # Erro de API antes de qualquer resposta: nenhuma chamada foi concluída,
        # mas o CHECK exige 1 ou 2 (houve 1 tentativa).
        chamadas=max(1, uso.chamadas),
        erro_mensagem=erro,
    )
    session.add(geracao)
    session.flush()  # obtém o id para ligar flashcards/questões (geracao_id)
    return geracao
