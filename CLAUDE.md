# CLAUDE.md — estuda-ai

Assistente de estudos para universitários (RAG sobre materiais, flashcards e questões
gerados por LLM, repetição espaçada SM-2, dashboard). Projeto de portfólio cujo
**objetivo principal é o autor aprender banco de dados** (PostgreSQL, modelagem,
índices, transações, busca vetorial).

## Como trabalhar neste projeto

- **Explique as decisões de banco.** Em commits e em `docs/` (`modelagem.md` para o
  schema, `busca-semantica.md` para busca/índices/transações do pipeline), diga o
  *porquê* (alternativas consideradas, custo/benefício), não só o quê. Didático,
  em português.
- **Commits pequenos, Conventional Commits em português** (`feat:`, `fix:`, `docs:`,
  `chore:`, `test:`, `refactor:`; escopo opcional, ex. `feat(db):`). Push ao fim de cada
  etapa concluída.
- Mostre um plano curto antes de mudanças grandes de schema.
- Ao mudar o schema, atualize `docs/modelagem.md` (diagrama ER e texto) no mesmo PR.

## Estado atual

Fases 1 (fundação + modelagem) e 2 (upload de PDF, embeddings, busca semântica/textual/
híbrida, experimento HNSW) concluídas. Próxima: fase 3 (geração de flashcards/questões
com a API da Anthropic). Roadmap no `README.md`.

## Comandos

```bash
docker compose up -d --build                       # sobe db + backend (aplica migrations)
docker compose exec backend pytest                 # testes (banco estuda_ai_test, embedder falso)
docker compose exec backend pytest -m modelo       # teste com o modelo real de embeddings
docker compose exec backend alembic check          # modelos x migrations em sincronia?
docker compose exec backend alembic revision -m "descricao"
docker compose exec db psql -U estuda_ai -d estuda_ai
docker compose exec backend python -m scripts.seed_experimento   # 50 mil trechos sintéticos
docker compose exec backend python -m scripts.experimento_hnsw   # regenera docs/experimentos/hnsw.md
```

Docker aqui é **Colima** (`colima start` se o socket não responder). Testes também
rodam no host com `uv run pytest`, sobrescrevendo `DATABASE_URL`/`TEST_DATABASE_URL`
com `localhost` no lugar de `db`.

Armadilhas de ambiente já encontradas:
- O `--reload` do uvicorn só funciona com `WATCHFILES_FORCE_POLLING` (já no compose): os
  eventos de arquivo do macOS não atravessam o virtiofs do Colima.
- `shm_size: 1gb` no serviço `db` é necessário para `CREATE INDEX` paralelo (HNSW).
- O shell é zsh: variável com várias flags sem aspas não é dividida (`curl $FLAGS` quebra).
  Use `bash <<'EOF'` ou scripts Python. `docker compose exec -T` dentro de um heredoc
  consome o stdin: use `</dev/null`.
- Nas migrations, nomes passados a `op.drop_constraint` vão com `op.f("...")`; sem isso a
  naming convention adiciona o prefixo de novo (`ck_materiais_ck_materiais_...`).

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
- Embeddings: `vector(384)` (constante `EMBEDDING_DIM`, modelo
  `intfloat/multilingual-e5-small`), índice HNSW `vector_cosine_ops` → consultas usam o
  operador `<=>`. Trocar de modelo = migration nova + reprocessar todos os trechos.
  O e5 exige os prefixos `"passage: "`/`"query: "` (já em `EmbedderE5`).
- Busca vetorial sempre com `set_config('hnsw.ef_search', ..., true)` >= LIMIT e
  `hnsw.iterative_scan = strict_order` (ver `_configurar_hnsw` em `app/servicos/busca.py`).
  Toda busca filtra por `trechos.disciplina_id` (cópia protegida pela FK composta
  `(material_id, disciplina_id) → materiais(id, disciplina_id)`).
- Full-text: configuração `portugues_unaccent` (constante `CONFIG_TEXTO`), coluna gerada
  `trechos.conteudo_tsv` + GIN. Nunca grave `conteudo_tsv` à mão.
- Desnormalizar só com proteção: preferir que o banco garanta a coerência (FK composta,
  coluna gerada); se não der, o código grava as duas cópias na mesma transação.
- Escritas que mexem em mais de uma tabela (ex. revisão SM-2: `INSERT revisoes` +
  `UPDATE flashcards`; trechos + status do material) vão na **mesma transação**.
- Transições de estado com compare-and-set: `UPDATE ... WHERE id = ? AND status = 'x'
  RETURNING`; 0 linhas = outro processo chegou antes. Trabalho pesado (CPU, rede, LLM)
  fica **fora** de transação aberta.
- Arquivos ficam no volume `uploads`; o banco guarda o caminho relativo. Disco e banco
  não têm transação comum: no upload, apagar o arquivo se o INSERT falhar; no delete,
  apagar o arquivo só depois do COMMIT.

## Convenções de código

- Python 3.12, SQLAlchemy 2.0 síncrono (`Mapped`/`mapped_column`) com psycopg 3.
- Dependências com `uv` (`uv add`, `uv add --dev`); `uv.lock` é commitado.
- Rotas em `app/routers/<recurso>.py`; lógica em `app/servicos/`; schemas Pydantic em
  `app/schemas.py`. SQL de busca escrito por extenso com `text()` (é objeto de estudo).
- Disciplina do usuário via dependência `DisciplinaDoUsuario` (404 se não for dele).
- Tarefas em background recebem a fábrica de sessões (`FabricaSessaoDep`), nunca a sessão
  da requisição. Modelo de embeddings via `EmbedderDep` (nos testes, `EmbedderFalso`).
- Usuário atual via dependência `UsuarioAtual` (provisório: header `X-Usuario-Id`;
  será trocado por autenticação sem mudar as rotas). Toda consulta filtra pelo dono;
  recurso de outro usuário → 404.
- Nomes de domínio em português (tabelas, colunas, funções, testes).
- Testes contra Postgres real, nunca SQLite. Cada teste roda numa transação desfeita
  no fim (ver `tests/conftest.py`). Regras do banco têm teste em `tests/test_schema.py`.
  Testes com o modelo real levam `@pytest.mark.modelo` (fora da execução padrão).
- PDFs de teste são gerados com `criar_pdf()` do conftest.

## Segredos

`.env` nunca é commitado (está no `.gitignore`). Novas variáveis entram no
`.env.example` com valor de exemplo. Chave futura: `ANTHROPIC_API_KEY` (fase 3).
