# CLAUDE.md — estuda-ai

Assistente de estudos para universitários (RAG sobre materiais, flashcards e questões
gerados por LLM, repetição espaçada SM-2, dashboard). Projeto de portfólio cujo
**objetivo principal é o autor aprender banco de dados** (PostgreSQL, modelagem,
índices, transações, busca vetorial).

## Como trabalhar neste projeto

- **Explique as decisões de banco.** Em commits e em `docs/` (`modelagem.md` para o
  schema, `busca-semantica.md` para busca/índices/transações do pipeline,
  `geracao-llm.md` para LLM/RAG/auditoria, `repeticao-espacada.md` para SM-2/fila/
  concorrência/fuso, `analytics.md` para as consultas analíticas, `seguranca.md` para
  auth/isolamento/injection/rate limit/privilégios/CI, `frontend.md` para BFF/cookies/
  contrato tipado/TanStack Query/gráficos, `deploy.md` para hospedagem/backup/operação),
  diga o
  *porquê* (alternativas consideradas, custo/benefício), não só o quê. Didático,
  em português.
- **Commits pequenos, Conventional Commits em português** (`feat:`, `fix:`, `docs:`,
  `chore:`, `test:`, `refactor:`; escopo opcional, ex. `feat(db):`). Push ao fim de cada
  etapa concluída.
- Mostre um plano curto antes de mudanças grandes de schema.
- Ao mudar o schema, atualize `docs/modelagem.md` (diagrama ER e texto) no mesmo PR.

## Estado atual

Fases 1 (fundação + modelagem), 2 (upload de PDF, embeddings, busca semântica/textual/
híbrida, experimento HNSW), 3 (RAG com a API da Anthropic: perguntar, flashcards,
questões, tentativas, auditoria de custos), 4 (SM-2, fila do dia, concorrência, fuso) e 5
(analytics com SQL avançado, views e materialized view) concluídas. Fase 6 em 3 sessões:
(1) segurança + CI, (2) frontend Next.js e (3) deploy **concluídas**. Produção: frontend
na Vercel (Root Directory `frontend`, região gru1, corpo de requisição máx. 4,5 MB → PDFs
até 4 MB no upload) + API/Postgres numa VM
Oracle Always Free (ARM, São Paulo, 1 OCPU/4 GB: a memória precisa ficar acima de 20% para a VM não ser "ociosa") escolhida pelo autor ("grátis, só para
eu usar"), com `docker-compose.prod.yml`. A criação da conta/VM/domínio é tarefa manual
do autor (`docs/deploy.md`, seção 4). Roadmap no `README.md`.
Pendente da fase 3: o teste real com a API (falta `ANTHROPIC_API_KEY` no `.env`). Exercícios de SQL por fase em `docs/exercicios.md` (sem respostas; o autor
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
docker compose exec backend python -m scripts.criar_usuario --email x@y.com --nome "X"  # única forma de criar conta
docker compose exec backend ruff check .                         # lint (o mesmo do CI)
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.30.1 git --redact --gitleaks-ignore-path /repo/.gitleaksignore /repo  # segredos no histórico (sem pipe!)
docker compose exec backend python -m scripts.seed_revisoes --senha-dev   # conta demo local p/ o frontend
docker compose exec -T backend python -m scripts.exportar_openapi > frontend/openapi.json  # contrato
npm --prefix frontend run dev        # http://localhost:3000 (frontend/.env.local: BACKEND_URL, BFF_SEGREDO)
npm --prefix frontend run tipos      # tipos TS a partir do openapi.json
npm --prefix frontend run lint && npm --prefix frontend run typecheck && npm --prefix frontend test && npm --prefix frontend run build
```

Seeds: `scripts/seed_experimento.py` (50 mil trechos sintéticos, fase 2) e
`scripts/seed_revisoes.py` (estudante@estuda-ai.local, 6 semanas de estudo simuladas; com
`--alunos 200` cria volume para o EXPLAIN, fase 5).

Docker aqui é **Colima** (`colima start` se o socket não responder). Testes também
rodam no host com `uv run pytest`, sobrescrevendo `DATABASE_URL`/`MIGRATION_DATABASE_URL`
com `localhost` no lugar de `db` (o banco de teste é sempre `<banco>_test`).

Armadilhas de ambiente já encontradas:
- O `--reload` do uvicorn só funciona com `WATCHFILES_FORCE_POLLING` (já no compose): os
  eventos de arquivo do macOS não atravessam o virtiofs do Colima.
- `shm_size: 1gb` no serviço `db` é necessário para `CREATE INDEX` paralelo (HNSW).
- O shell é zsh: variável com várias flags sem aspas não é dividida (`curl $FLAGS` quebra).
  Use `bash <<'EOF'` ou scripts Python. `docker compose exec -T` dentro de um heredoc
  consome o stdin: use `</dev/null`.
- Nas migrations, nomes passados a `op.drop_constraint` vão com `op.f("...")`; sem isso a
  naming convention adiciona o prefixo de novo (`ck_materiais_ck_materiais_...`).
- Não rode lint com pipe (`ruff ... | tail`): o pipe engole o código de saída.
- FastAPI 0.14x: `app.routes` não lista as rotas dos routers incluídos; use
  `app.openapi()["paths"]` (meta-testes de `test_isolamento.py`).
- gitleaks: allowlist global com `paths` pula o ARQUIVO inteiro, mesmo com
  `condition = "AND"`. Use só `regexes` com `regexTarget = "line"` ancorado.
- Preview no app desktop: o `launch.json` lido é o da pasta da sessão
  (`~/claude projetos/.claude/launch.json`, entrada `estuda-ai-frontend`), não o do repo.
- Next 16: `LayoutProps`/`PageProps`/`RouteContext` são gerados em `.next/types`; por isso
  `typecheck` = `next typegen && tsc`. Middleware agora se chama `proxy.ts`.
- O SDK da Anthropic sem chave lança `TypeError` (não `AnthropicError`): `ClienteLLM.gerar`
  confere a credencial antes.

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
- Escritas que mexem em mais de uma tabela (ex. revisão SM-2: `UPDATE revisoes` +
  `INSERT historico_revisoes`; trechos + status do material) vão na **mesma transação**.
- Transições de estado com compare-and-set: `UPDATE ... WHERE id = ? AND status = 'x'
  RETURNING`; 0 linhas = outro processo chegou antes. Trabalho pesado (CPU, rede, LLM)
  fica **fora** de transação aberta.
- Arquivos ficam no volume `uploads`; o banco guarda o caminho relativo. Disco e banco
  não têm transação comum: no upload, apagar o arquivo se o INSERT falhar; no delete,
  apagar o arquivo só depois do COMMIT.

## Segurança (fase 6)

Detalhes e o porquê em `docs/seguranca.md`.

- **Dois papéis no banco.** A API e os testes conectam como `estuda_ai_app` (`DATABASE_URL`);
  migrations e scripts de admin/seed como o dono (`MIGRATION_DATABASE_URL`). Tabela nova =
  `GRANT` explícito na migration que a cria (não há `DEFAULT PRIVILEGES`) + atualizar
  `ESPERADO` em `tests/test_privilegios.py`. Tabelas de histórico/auditoria: só
  `SELECT, INSERT`. Nos testes, dado "no passado" vai no INSERT (`criado_em=...`), não num
  `UPDATE` posterior. Teste que precisa de DDL ou de apagar usuários usa `engine_dono`/
  `session_dono`.
- O que exige ser dono (ex.: `REFRESH MATERIALIZED VIEW`) vira função `SECURITY DEFINER` com
  `SET search_path = pg_catalog, public, pg_temp` + `REVOKE EXECUTE ... FROM PUBLIC` + `GRANT`
  ao app.
- **Rota nova** → caso em `CASOS_404` ou `CASOS_LISTAS` de `tests/test_isolamento.py` (o
  meta-teste falha sem isso). Rotas que chamam o LLM recebem `_: ProtecaoIA` **depois** de
  `DisciplinaDoUsuario` (dado alheio continua 404 e não gasta cota).
- **SQL:** valores sempre como bind parameter. Identificador dinâmico só via allowlist
  (`_coluna()` em `analytics.py`) ou `psycopg.sql.Identifier`; comandos utilitários sem bind
  (`ALTER ROLE ... PASSWORD`) com `psycopg.sql.Literal`.
- Senhas: argon2id (`app/servicos/auth.py`), mínimo 12 caracteres. JWT HS256 com
  `algorithms=["HS256"]` fixo; `JWT_SECRET` é `SecretStr`. Sem cadastro público:
  `scripts/criar_usuario.py`.
- Rate limit: UPSERT em `limites_taxa` (UNLOGGED, janela fixa com `date_bin`), commit imediato.
  Cota diária contada em `geracoes`.
- CI (`.github/workflows/ci.yml`): ruff, migrations nos dois sentidos, `alembic check`, pytest
  (sem o grupo de dependências `modelo`) e gitleaks no histórico inteiro. Rode testes e build
  antes de cada push. Dependência pesada nova que só o modelo usa vai no grupo `modelo`.

## Frontend (fase 6)

Detalhes e o porquê em `docs/frontend.md`.

- Next.js 16 + TypeScript + Tailwind 4 + TanStack Query + Recharts, em `frontend/`. Leia
  `node_modules/next/dist/docs/` antes de usar API do Next (mudou muito: `proxy.ts`,
  Cache Components, `PageProps`).
- **O navegador nunca fala com a API direto.** `/api/sessao` (login/logout, grava o cookie
  httpOnly) e `/api/[...caminho]` (repasse com Bearer). Regras de segurança do BFF são
  funções puras em `src/lib/bff.ts` com testes em `bff.test.ts`: rota nova da API que o
  frontend usa entra na allowlist de lá.
- Contrato tipado: mudou schema/rota no backend → `exportar_openapi` + `npm run tipos` e
  commit dos dois arquivos (o CI confere).
- Dados via hooks em `src/lib/consultas.ts` (chaves hierárquicas em `chaves`); mutações
  invalidam o que mudou. Componentes que leem a URL (`useParams`/`usePathname`) ficam
  dentro de `<Suspense>` (exigência do Cache Components).
- Visual "caderno": tokens em `globals.css` (`papel`, `tinta`, `apagado`, `fio`, `acento`,
  `tarja`...), fios em vez de cartões, sem sombras/pílulas. Componentes base em
  `src/components/ui.tsx`. Todo dado tem estados carregando/erro/vazio.
- Gráficos (`src/components/graficos.tsx`): forma pelo trabalho do dado, um eixo, marcas
  finas, tabela equivalente em toda figura; cores `--grafico-destaque/--grafico-contexto`
  validadas (claro e escuro) com o validador da skill de dataviz.

## Produção (fase 6)

Detalhes em `docs/deploy.md`.

- `docker-compose.prod.yml` (projeto `estuda-ai-prod`), modo padrão "Vercel + VM": o Caddy
  (`deploy/Caddyfile`) publica só a API e responde 404 a quem não traz o `X-BFF-Segredo`
  (exceto `/health`). Modo "tudo na VM": `CADDYFILE=Caddyfile.completo` + perfil
  `frontend-na-vm`. Só o Caddy publica portas;
  redes `dados` (interna, db↔backend), `app` (backend↔frontend) e `borda`
  (frontend↔caddy). Serviço novo entra só nas redes de que precisa; nada de `ports` além
  do Caddy. O frontend recebe só `BACKEND_URL` e `BFF_SEGREDO`.
- Backend em produção: `UV_SYNC_ARGS=--no-dev`, `user: 10001` (pastas dos volumes criadas
  com esse dono no Dockerfile), CMD do Dockerfile (migrations → papel_app → uvicorn).
- `.env` de produção só nasce no servidor (`deploy/gerar-env.sh`, segredos via openssl,
  chmod 600); nunca pedir nem escrever segredos no chat.
- Backup: `deploy/backup.sh` (cron 04:00 UTC, `pg_dump -Fc` + tar dos PDFs, 14 dias);
  restore: `deploy/restaurar.sh` (cria o papel antes; `--clean --if-exists
  --single-transaction`). Mudou volume ou nome de projeto? Ajuste os dois scripts.
- Antes de mexer no deploy, teste a pilha de produção no Mac (ARM como a VM) numa cópia
  em `~/` (o Colima só monta a home), com `PORTA_HTTP=8080 PORTA_HTTPS=8443` e
  `DOMINIO=localhost`.

## Analytics (fase 5)

- Toda consulta analítica é SQL explícito em `app/servicos/analytics.py` (`text()`), comentada
  com o conceito que ensina; documentada em `docs/analytics.md`.
- Histórico agregado lê `mv_respostas_diarias` (e a rota devolve `atualizado_em`); o que precisa
  refletir "agora" (sequência, previsão, ranking, custos) é ao vivo. `POST /analytics/atualizar`
  faz `REFRESH ... CONCURRENTLY` (exige o índice único da MV) pela função SECURITY DEFINER
  `atualizar_mv_respostas_diarias()`.
- Dia/semana/mês sempre no fuso do usuário (`vw_respostas.dia` já vem local).
- Séries para gráfico são **densificadas** (`generate_series` + `LEFT JOIN`) antes de janelas e
  `LAG`. Taxas = razão das somas. Período limitado a 731 dias.
- Filtros de período sargable: coluna crua + limite numa subconsulta escalar (`_periodo()`).
- Filtre pelo usuário ANTES de `DISTINCT`/agregações globais; não materialize CTEs com filtro
  de fora.
- Testes de analytics montam dados controlados com resultado conhecido
  (`tests/test_analytics.py`) e chamam `analytics.atualizar_mv()` antes de ler a MV.

## Repetição espaçada (fase 4)

- Estado do SM-2 em `revisoes` (1:1, PK = FK, criado por trigger no INSERT do card);
  histórico em `historico_revisoes` (só INSERT; trigger recusa UPDATE). `flashcards` não
  tem mais colunas do SM-2.
- SM-2 usado pela API: `app/servicos/sm2.py` (Decimal + ROUND_HALF_UP). A função `sm2()`
  em PL/pgSQL é só experimento; `tests/test_sm2.py` confere que as duas concordam. Mudou a
  fórmula? Mude as duas (nova migration para a função).
- Concorrência: controle otimista (`UPDATE ... WHERE versao = :v RETURNING`; 0 linhas =
  409). Testes de concorrência real em `tests/test_concorrencia.py` (conexões próprias,
  dados commitados, `Barrier`).
- Tempo: `timestamptz` sempre; "hoje" = próxima meia-noite no `usuarios.fuso_horario`
  (`SQL_FIM_DE_HOJE` em `app/servicos/revisao.py`). Nunca `now()::date` nem o fuso do servidor.
  Funções de serviço aceitam `agora=` para testes.
- Fila do dia: índice `(disciplina_id, proxima_revisao)`; a fila do usuário inteiro usa
  `CROSS JOIN LATERAL` por disciplina (top-N por grupo). Evite `(:p IS NULL OR col = :p)`:
  escreva duas consultas.

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
- Usuário atual via dependência `UsuarioAtual` (JWT Bearer, confere `versao_token`). Toda
  consulta filtra pelo dono; recurso de outro usuário → 404 (nunca 403).
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
