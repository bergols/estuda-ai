"""materiais: status 'concluido' e metadados do arquivo

1. O status 'processado' passa a se chamar 'concluido'. Aqui se paga a escolha
   de text + CHECK em vez de ENUM: basta trocar a constraint e atualizar os dados.
   Com ENUM, renomear um valor exige ALTER TYPE ... RENAME VALUE, e remover um
   valor exige recriar o tipo e reescrever a coluna.
   A ordem importa: (a) remove o CHECK antigo, (b) converte os dados,
   (c) cria o CHECK novo. Com o CHECK antigo ainda ativo, o UPDATE para
   'concluido' seria recusado.

2. Colunas novas para o processamento em background:
   - caminho_arquivo: onde o PDF está no volume. O arquivo em si NÃO vai para o banco.
   - erro_mensagem: por que o processamento falhou.
   - num_paginas, processado_em.

3. Constraints condicionais (uma regra "se A então B" vira "NOT A OR B"):
   - erro_mensagem só existe quando status = 'erro'.
   - material do tipo 'pdf' precisa ter caminho_arquivo.

Revision ID: 0487cfbe0ee2
Revises: 4403e2d60f1d
Create Date: 2026-10-07 01:46:12.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0487cfbe0ee2"
down_revision: Union[str, Sequence[str], None] = "4403e2d60f1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _trocar_status(antigo: str, novo: str) -> None:
    # op.f(): o nome já está completo; sem ele a naming convention adicionaria
    # o prefixo de novo (ck_materiais_ck_materiais_...).
    op.drop_constraint(op.f("ck_materiais_status_valido"), "materiais", type_="check")
    op.execute(f"UPDATE materiais SET status = '{novo}' WHERE status = '{antigo}'")
    op.create_check_constraint(
        op.f("ck_materiais_status_valido"),
        "materiais",
        f"status IN ('pendente', 'processando', '{novo}', 'erro')",
    )


def upgrade() -> None:
    _trocar_status("processado", "concluido")

    op.add_column("materiais", sa.Column("caminho_arquivo", sa.Text(), nullable=True))
    op.add_column("materiais", sa.Column("erro_mensagem", sa.Text(), nullable=True))
    op.add_column("materiais", sa.Column("num_paginas", sa.Integer(), nullable=True))
    op.add_column(
        "materiais", sa.Column("processado_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_materiais_num_paginas_positivo"), "materiais", "num_paginas > 0"
    )
    op.create_check_constraint(
        op.f("ck_materiais_erro_so_com_status_erro"),
        "materiais",
        "erro_mensagem IS NULL OR status = 'erro'",
    )
    op.create_check_constraint(
        op.f("ck_materiais_pdf_tem_arquivo"),
        "materiais",
        "tipo <> 'pdf' OR caminho_arquivo IS NOT NULL",
    )


def downgrade() -> None:
    # DROP COLUMN leva junto os CHECKs que dependem só da coluna, mas
    # ck_materiais_pdf_tem_arquivo também usa "tipo"; apagamos explicitamente.
    op.drop_constraint(op.f("ck_materiais_pdf_tem_arquivo"), "materiais", type_="check")
    op.drop_constraint(op.f("ck_materiais_erro_so_com_status_erro"), "materiais", type_="check")
    op.drop_constraint(op.f("ck_materiais_num_paginas_positivo"), "materiais", type_="check")
    for coluna in ("processado_em", "num_paginas", "erro_mensagem", "caminho_arquivo"):
        op.drop_column("materiais", coluna)
    _trocar_status("concluido", "processado")
