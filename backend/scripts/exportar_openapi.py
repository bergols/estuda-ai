"""Escreve o contrato da API (OpenAPI) em JSON na saída padrão.

    docker compose exec -T backend python -m scripts.exportar_openapi > frontend/openapi.json

O frontend gera os tipos TypeScript a partir deste arquivo (npm run tipos). O CI
exporta de novo e compara com o commitado: mudou um schema ou uma rota e esqueceu de
regenerar, o CI falha antes que o frontend quebre em produção.
"""

import json
import sys

from app.main import app


def main() -> None:
    json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
