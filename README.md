# estuda-ai

Assistente de estudos para universitários. Você cadastra suas disciplinas, envia
materiais (PDFs, anotações) e o sistema faz busca semântica no conteúdo, gera
flashcards e questões com IA, agenda revisões com repetição espaçada e mostra seu
desempenho por disciplina e tópico.

É um projeto de portfólio com um objetivo paralelo: **aprender banco de dados a fundo**
(PostgreSQL, modelagem, índices, transações e busca vetorial). Por isso as decisões de
banco estão explicadas nos commits, em [`docs/modelagem.md`](docs/modelagem.md) e em
[`docs/busca-semantica.md`](docs/busca-semantica.md).

## Stack

| Camada | Tecnologia |
|---|---|
| Banco | PostgreSQL 16 + [pgvector](https://github.com/pgvector/pgvector) (busca vetorial, índice HNSW) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic |
| Busca | embeddings locais (`intfloat/multilingual-e5-small`, 384 dimensões, sentence-transformers) + full-text do Postgres |
| PDF | PyMuPDF |
| IA | API da Anthropic (geração de flashcards e questões, fase 3) |
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

O backend aplica as migrations sozinho ao subir (`alembic upgrade head`). A primeira
subida demora: a imagem tem ~1,7 GB (torch para CPU). O modelo de embeddings (~470 MB)
é baixado no primeiro upload e fica no volume `modelos`. Para baixar antes:

```bash
docker compose exec backend python -m app.servicos.embeddings
```

Teste:

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
Eles usam um embedder falso (rápido, sem baixar o modelo). O teste com o modelo real é
separado:

```bash
docker compose exec backend pytest -m modelo
```

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

Envie um PDF (responde 202; o processamento roda em background):

```bash
curl -X POST localhost:8000/disciplinas/1/materiais -H 'X-Usuario-Id: 1' -F 'arquivo=@aula.pdf;type=application/pdf'
```

Acompanhe o `status` (`pendente` → `processando` → `concluido` ou `erro`):

```bash
curl localhost:8000/disciplinas/1/materiais -H 'X-Usuario-Id: 1'
```

Busque (`modo` = `semantica`, `textual` ou `hibrida`):

```bash
curl -G localhost:8000/disciplinas/1/busca -H 'X-Usuario-Id: 1' --data-urlencode 'q=como desfazer uma transação' -d k=5 -d modo=hibrida
```

### Experimento de desempenho

Carrega 50 mil trechos sintéticos e compara a busca com e sem o índice HNSW
(resultados em [`docs/experimentos/hnsw.md`](docs/experimentos/hnsw.md)):

```bash
docker compose exec backend python -m scripts.seed_experimento
```

```bash
docker compose exec backend python -m scripts.experimento_hnsw
```

O seed cria o usuário `experimento@estuda-ai.local`; apagá-lo remove tudo em cascata.

## Estrutura

```
estuda-ai/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI + /health
│   │   ├── models.py        # modelos SQLAlchemy (espelho do schema)
│   │   ├── schemas.py       # contratos Pydantic da API
│   │   ├── deps.py          # dependências (sessão, usuário atual, embedder)
│   │   ├── routers/         # rotas por recurso
│   │   └── servicos/        # pdf, chunking, embeddings, processamento, busca
│   ├── alembic/versions/    # migrations: a fonte da verdade do schema
│   ├── scripts/             # seed e experimento HNSW
│   └── tests/
├── docs/
│   ├── modelagem.md         # diagrama ER e decisões de banco
│   ├── busca-semantica.md   # embeddings, pgvector, HNSW, full-text, híbrida
│   └── experimentos/        # resultados gerados por script
└── frontend/                # fase 6
```

## Roadmap

- [x] **Fase 1: fundação e modelagem.** Docker Compose, schema com 8 tabelas,
  constraints, índices B-tree e HNSW, migration inicial, `/health`, CRUD de
  disciplinas e testes.
- [x] **Fase 2: materiais e busca semântica.** Upload de PDF com processamento
  transacional em background, chunking com sobreposição, embeddings locais, busca
  semântica (pgvector + HNSW), textual (tsvector + GIN) e híbrida (RRF), e experimento
  com `EXPLAIN ANALYZE`.
- [ ] **Fase 3: geração com IA.** Flashcards e questões gerados pela API da
  Anthropic a partir dos trechos (RAG).
- [ ] **Fase 4: repetição espaçada.** Algoritmo SM-2, com a revisão gravada em
  transação (histórico em `revisoes` + estado atual em `flashcards`).
- [ ] **Fase 5: dashboard.** Queries analíticas com `GROUP BY`, window functions e
  views. Normalização de tópicos numa tabela própria.
- [ ] **Fase 6: frontend e otimização.** Next.js, autenticação, fila de processamento
  robusta (`FOR UPDATE SKIP LOCKED`), limpeza de arquivos órfãos, ajuste de
  `ef_search`/`m` com dados reais e deploy.
