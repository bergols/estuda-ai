# CLAUDE.md — estuda-ai

Assistente de estudos para universitários (RAG sobre materiais, flashcards e questões
gerados por LLM, repetição espaçada SM-2, dashboard). Projeto de portfólio cujo
**objetivo principal é o autor aprender banco de dados** (PostgreSQL, modelagem,
índices, transações, busca vetorial).

## Como trabalhar neste projeto

- **Explique as decisões de banco.** Em commits e em `docs/` (`modelagem.md` para o
  schema, `busca-semantica.md` para busca/índices/transações do pipeline,
  `geracao-llm.md` para LLM/RAG/auditoria), diga o
  *porquê* (alternativas consideradas, custo/benefício), não só o quê. Didático,
  em português.
- **Commits pequenos, Conventional Commits em português** (`feat:`, `fix:`, `docs:`,
  `chore:`, `test:`, `refactor:`; escopo opcional, ex. `feat(db):`). Push ao fim de cada
  etapa concluída.
- Mostre um plano curto antes de mudanças grandes de schema.
- Ao mudar o schema, atualize `docs/modelagem.md` (diagrama ER e texto) no mesmo PR.

## Estado atual

Fases 1 (fundação + modelagem), 2 (upload de PDF, embeddings, busca semântica/textual/
híbrida, experimento HNSW) e 3 (RAG com a API da Anthropic: perguntar, flashcards,
questões, tentativas, auditoria de custos) concluídas. Próxima: fase 4 (SM-2). Roadmap no
`README.md`. Exercícios de SQL por fase em `docs/exercicios.md` (sem respostas; o autor
preenche "Minha resposta:").

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
- Regras que envolvem várias linhas e não cabem em CHECK: constraint trigger
  `DEFERRABLE INITIALLY DEFERRED` (ex.: `ck_questoes_alternativas_validas`), levantando o erro
  com `USING CONSTRAINT = '<nome>'`. O teardown dos testes roda `SET CONSTRAINTS ALL IMMEDIATE`.
- Relação N:N = tabela associativa com PK composta + índice na 2ª coluna
  (`flashcard_trechos`, `questao_trechos`).
- Escritas que mexem em mais de uma tabela (ex. revisão SM-2: `INSERT revisoes` +
  `UPDATE flashcards`; trechos + status do material) vão na **mesma transação**.
- Transições de estado com compare-and-set: `UPDATE ... WHERE id = ? AND status = 'x'
  RETURNING`; 0 linhas = outro processo chegou antes. Trabalho pesado (CPU, rede, LLM)
  fica **fora** de transação aberta.
- Arquivos ficam no volume `uploads`; o banco guarda o caminho relativo. Disco e banco
  não têm transação comum: no upload, apagar o arquivo se o INSERT falhar; no delete,
  apagar o arquivo só depois do COMMIT.

## LLM (fase 3)

- SDK `anthropic` (1.x). Modelo em `ANTHROPIC_MODEL` (padrão `claude-haiku-4-5-20251001`);
  a chave `ANTHROPIC_API_KEY` só no `.env`, lida pelo SDK (nunca no `Settings`, nunca em
  commit). Rotas recebem o cliente via `LLMDep`.
- Toda geração passa por `ClienteLLM.gerar()` (`app/servicos/llm.py`): saída estruturada
  (`output_config.format` a partir de Pydantic), validação + 1 nova tentativa com o erro,
  `ErroGeracao` com status `erro_validacao`/`erro_api`.
- RAG: contexto da busca híbrida ou de um material (até 30 trechos espalhados), trechos
  rotulados `T1..Tk` e escapados (são dados, não instruções); citações validadas.
- Feche a transação de leitura (`session.commit()`) antes de chamar o LLM.
- Toda chamada gera uma linha em `geracoes` (`app/servicos/auditoria.py`): na mesma
  transação do conteúdo em caso de sucesso; numa transação só dela em caso de falha.
  Preços em `PRECOS_POR_MILHAO` (`llm.py`).
- Deduplicação de flashcards: embedding de frente + verso (`embed_simetrico`, prefixo
  `query:`), limiar `LIMIAR_DUPLICATA = 0.95` (calibrado em `docs/geracao-llm.md`), com
  `pg_advisory_xact_lock(1, disciplina_id)`.
- **Nenhum teste chama a API real**: o fixture `anthropic_falso` (`tests/fakes.py`) é
  injetado em todo `client`; cada teste põe as respostas em `.roteiro`.

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
`.env.example` com valor de exemplo; chaves entram **comentadas** e sem valor real
(`# ANTHROPIC_API_KEY=sk-ant-...`), porque uma variável vazia conta como "definida" para o
SDK.
