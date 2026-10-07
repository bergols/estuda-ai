# CLAUDE.md — estuda-ai

Assistente de estudos para universitários (RAG sobre materiais, flashcards e questões
gerados por LLM, repetição espaçada SM-2, dashboard). Projeto de portfólio cujo
**objetivo principal é o autor aprender banco de dados** (PostgreSQL, modelagem,
índices, transações, busca vetorial).

## Como trabalhar neste projeto

- **Explique as decisões de banco.** Em commits e em `docs/modelagem.md`, diga o
  *porquê* (alternativas consideradas, custo/benefício), não só o quê. Didático,
  em português.
- **Commits pequenos, Conventional Commits em português** (`feat:`, `fix:`, `docs:`,
  `chore:`, `test:`, `refactor:`; escopo opcional, ex. `feat(db):`). Push ao fim de cada
  etapa concluída.
- Mostre um plano curto antes de mudanças grandes de schema.
- Ao mudar o schema, atualize `docs/modelagem.md` (diagrama ER e texto) no mesmo PR.

## Estado atual

Fase 1 concluída (fundação + modelagem). Roadmap das fases 2–6 no `README.md`.

## Comandos

```bash
docker compose up -d --build                       # sobe db + backend (aplica migrations)
docker compose exec backend pytest                 # testes (banco estuda_ai_test)
docker compose exec backend alembic check          # modelos x migrations em sincronia?
docker compose exec backend alembic revision -m "descricao"
docker compose exec db psql -U estuda_ai -d estuda_ai
```

Docker aqui é **Colima** (`colima start` se o socket não responder). Testes também
rodam no host com `uv run pytest`, sobrescrevendo `DATABASE_URL`/`TEST_DATABASE_URL`
com `localhost` no lugar de `db`.

## Convenções de banco (seguir nas próximas migrations)

- **Migrations escritas à mão**, uma por mudança lógica, com comentários explicando a
  decisão. Depois de escrever, rode `alembic check` (deve dizer "No new upgrade
  operations detected") e teste `downgrade -1` + `upgrade head`.
- **Nunca edite uma migration já commitada/aplicada**; crie uma nova.
- `app/models.py` espelha a migration. Mudou um, muda o outro.
- PK: `bigint GENERATED ALWAYS AS IDENTITY`.
- Datas: sempre `timestamptz`. Tabelas mutáveis têm `criado_em` + `atualizado_em`
  (este mantido pelo trigger `definir_atualizado_em()`; ao criar tabela mutável nova,
  crie o trigger `trg_<tabela>_atualizado_em`). Tabelas de histórico são só-INSERT.
- "Enums": `text` + `CHECK (col IN (...))`, não `CREATE TYPE ... AS ENUM`.
- **Toda constraint tem nome explícito**, seguindo `NAMING_CONVENTION` em `models.py`
  (`pk_`, `fk_<tab>_<col>_<ref>`, `uq_`, `ck_<tab>_<regra>`). A API mapeia
  `IntegrityError` → HTTP pelo nome da constraint (`constraint_violada()` em `app/db.py`).
- **Toda FK precisa de índice** (o Postgres não cria sozinho). Pode ser a 1ª coluna de
  um índice composto/UNIQUE existente. FK opcional → índice parcial `WHERE col IS NOT NULL`.
- ON DELETE: `CASCADE` na hierarquia de posse; `SET NULL` quando o filho deve
  sobreviver à origem (ex. `trecho_id`). ORM com `passive_deletes=True`.
- Unicidade e integridade **no banco**; nada de "SELECT para checar antes do INSERT".
- Embeddings: `vector(1024)` (constante `EMBEDDING_DIM`), índice HNSW `vector_cosine_ops`
  → consultas devem usar o operador `<=>`.
- Escritas que mexem em mais de uma tabela (ex. revisão SM-2: `INSERT revisoes` +
  `UPDATE flashcards`) vão na **mesma transação**.

## Convenções de código

- Python 3.12, SQLAlchemy 2.0 síncrono (`Mapped`/`mapped_column`) com psycopg 3.
- Dependências com `uv` (`uv add`, `uv add --dev`); `uv.lock` é commitado.
- Rotas em `app/routers/<recurso>.py`; schemas Pydantic em `app/schemas.py`.
- Usuário atual via dependência `UsuarioAtual` (provisório: header `X-Usuario-Id`;
  será trocado por autenticação sem mudar as rotas). Toda consulta filtra pelo dono;
  recurso de outro usuário → 404.
- Nomes de domínio em português (tabelas, colunas, funções, testes).
- Testes contra Postgres real, nunca SQLite. Cada teste roda numa transação desfeita
  no fim (ver `tests/conftest.py`). Regras do banco têm teste em `tests/test_schema.py`.

## Segredos

`.env` nunca é commitado (está no `.gitignore`). Novas variáveis entram no
`.env.example` com valor de exemplo. Chaves futuras: `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY`.
