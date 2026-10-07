# estuda-ai

Assistente de estudos para universitários. Você cadastra suas disciplinas, envia
materiais (PDFs, anotações) e o sistema faz busca semântica no conteúdo, gera
flashcards e questões com IA, agenda revisões com repetição espaçada e mostra seu
desempenho por disciplina e tópico.

É um projeto de portfólio com um objetivo paralelo: **aprender banco de dados a fundo**
(PostgreSQL, modelagem, índices, transações e busca vetorial). Por isso as decisões de
banco estão explicadas nos commits e em [`docs/modelagem.md`](docs/modelagem.md).

## Stack

| Camada | Tecnologia |
|---|---|
| Banco | PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) (busca vetorial, índice HNSW) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic |
| IA | API da Anthropic (geração), embeddings de 1024 dimensões (Voyage AI ou bge-m3) |
| Frontend | Next.js + TypeScript (fase 6) |
| Infra | Docker Compose |
| Testes | pytest contra Postgres real |

## Como rodar

Pré-requisito: Docker com Compose. No macOS sem Docker Desktop, o
[Colima](https://github.com/abiosoft/colima) resolve:

```bash
brew install colima docker docker-compose
```

```bash
colima start
```

Depois, na raiz do projeto:

```bash
cp .env.example .env
```

Edite o `.env` e troque `troque-esta-senha` (nas três linhas) por uma senha sua. Então:

```bash
docker compose up --build
```

O backend aplica as migrations sozinho ao subir (`alembic upgrade head`). Teste:

```bash
curl localhost:8000/health
```

Resposta esperada: `{"status":"ok","banco":"ok","pgvector":"0.8.7"}`.
A documentação interativa da API fica em http://localhost:8000/docs.

### Testes

Com os containers no ar:

```bash
docker compose exec backend pytest
```

Os testes criam e usam um banco separado (`estuda_ai_test`), então não mexem nos seus dados.

### Explorar o banco

```bash
docker compose exec db psql -U estuda_ai -d estuda_ai
```

Comandos úteis no psql: `\dt` (tabelas), `\d+ flashcards` (estrutura completa),
`\di` (índices).

### Experimentar a API

Ainda não há login: o usuário é identificado pelo header `X-Usuario-Id` (provisório).

```bash
curl -X POST localhost:8000/usuarios -H 'Content-Type: application/json' -d '{"nome":"Ana","email":"ana@furg.br"}'
```

```bash
curl -X POST localhost:8000/disciplinas -H 'Content-Type: application/json' -H 'X-Usuario-Id: 1' -d '{"nome":"Banco de Dados"}'
```

## Estrutura

```
estuda-ai/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI + /health
│   │   ├── models.py        # modelos SQLAlchemy (espelho do schema)
│   │   ├── schemas.py       # contratos Pydantic da API
│   │   ├── deps.py          # dependências (sessão, usuário atual)
│   │   └── routers/         # rotas por recurso
│   ├── alembic/versions/    # migrations: a fonte da verdade do schema
│   └── tests/
├── docs/
│   └── modelagem.md         # diagrama ER e decisões de banco
└── frontend/                # fase 6
```

## Roadmap

- [x] **Fase 1: fundação e modelagem.** Docker Compose, schema com 8 tabelas,
  constraints, índices B-tree e HNSW, migration inicial, `/health`, CRUD de
  disciplinas e testes.
- [ ] **Fase 2: materiais e busca semântica.** Upload de PDF, extração de texto,
  chunking, embeddings e busca por similaridade com pgvector (operador `<=>`,
  filtro por disciplina com `hnsw.iterative_scan`).
- [ ] **Fase 3: geração com IA.** Flashcards e questões gerados pela API da
  Anthropic a partir dos trechos (RAG).
- [ ] **Fase 4: repetição espaçada.** Algoritmo SM-2, com a revisão gravada em
  transação (histórico em `revisoes` + estado atual em `flashcards`).
- [ ] **Fase 5: dashboard.** Queries analíticas com `GROUP BY`, window functions e
  views. Normalização de tópicos numa tabela própria.
- [ ] **Fase 6: frontend e otimização.** Next.js, autenticação, carga de dados de
  teste, otimização com `EXPLAIN ANALYZE` e deploy.
