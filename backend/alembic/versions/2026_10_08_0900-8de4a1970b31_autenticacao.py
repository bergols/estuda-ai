"""autenticação: usuarios.senha_hash e usuarios.versao_token

senha_hash: o hash argon2id da senha (NUNCA a senha). Nullable de propósito:
NULL = conta sem login (os alunos simulados do seed e os usuários dos testes). É
a resposta ao exercício deixado na fase 1 ("como adicionar uma coluna NOT NULL a
uma tabela que já tem linhas?"): aqui a coluna nem precisa ser NOT NULL, porque
"sem senha" é um estado válido, e o CHECK garante o formato quando há senha.

CHECK senha_hash LIKE '$argon2id$%': o banco recusa qualquer coisa que não seja
um hash argon2id, inclusive uma senha em texto puro gravada por engano por um
script. Defesa em profundidade: a aplicação já faz o hash, e o banco confere.

versao_token: vai dentro de todo JWT emitido. A cada requisição, o token só vale
se a versão dele for igual à do banco. Incrementar a versão invalida de uma vez
TODOS os tokens do usuário ("sair de todos os dispositivos", troca de senha). É
a forma barata de revogar JWT: um JWT sozinho vale até expirar, porque o servidor
não guarda estado nenhum sobre ele.

Revision ID: 8de4a1970b31
Revises: dfadb3e4bcb7
Create Date: 2026-10-08 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8de4a1970b31"
down_revision: Union[str, Sequence[str], None] = "dfadb3e4bcb7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("senha_hash", sa.Text(), nullable=True))
    op.add_column(
        "usuarios", sa.Column("versao_token", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint(
        op.f("ck_usuarios_senha_hash_argon2id"), "usuarios", "senha_hash LIKE '$argon2id$%'"
    )
    op.create_check_constraint(
        op.f("ck_usuarios_versao_token_nao_negativa"), "usuarios", "versao_token >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_usuarios_versao_token_nao_negativa"), "usuarios", type_="check")
    op.drop_constraint(op.f("ck_usuarios_senha_hash_argon2id"), "usuarios", type_="check")
    op.drop_column("usuarios", "versao_token")
    op.drop_column("usuarios", "senha_hash")
