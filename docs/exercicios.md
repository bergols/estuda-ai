# Exercícios de SQL — estuda-ai

Exercícios para fixar os conceitos de banco de cada fase, usando as tabelas do projeto.
Sem respostas: escreva a sua embaixo de cada um e, se quiser, peça uma correção.

Para abrir o banco:

```bash
docker compose exec db psql -U estuda_ai -d estuda_ai
```

Vários exercícios usam os dados do seed (50 mil trechos sintéticos):

```bash
docker compose exec backend python -m scripts.seed_experimento
```

> **Regra de ouro:** sempre que um exercício alterar dados, trabalhe dentro de
> `BEGIN; ... ROLLBACK;`. O DDL do Postgres também é transacional, então até um
> `DROP INDEX` volta com o `ROLLBACK`.

---

## Fase 1 — Modelagem, constraints e índices

Material de apoio: [`modelagem.md`](modelagem.md).

### 1.1 Preveja antes de rodar: quais INSERTs falham?

Dentro de uma transação, crie um usuário e uma disciplina. Depois **escreva sua
previsão** (passa ou falha? qual constraint?) para cada item abaixo, e só então execute:

- (a) um segundo usuário com o mesmo e-mail do primeiro, mas com letras maiúsculas;
- (b) um `UPDATE` que põe `facilidade = 1.29` no estado de um card (`revisoes`);
- (c) um flashcard sem dizer nada sobre o SM-2: o que aparece em `revisoes` para ele, e quem
  criou essa linha?
- (d) um trecho com `pagina = NULL` e outro com `pagina = 0`;
- (e) uma linha em `historico_revisoes` com `proxima_revisao_nova` anterior a `revisado_em`.

> Desde a Fase 4, o estado do SM-2 fica em `revisoes` (1:1 com o card) e o histórico em
> `historico_revisoes`; este exercício já usa o esquema atual.

Perguntas: por que (d) se comporta diferente nos dois casos? O que acontece com os
comandos seguintes depois do primeiro erro dentro da transação, e como continuar sem
perder o que já foi feito (dica: `SAVEPOINT`)?

**Minha resposta:**

```sql

```

### 1.2 Até onde vai o CASCADE?

Escolha uma disciplina com conteúdo (ex.: a "Pequena 01" do seed). Dentro de
`BEGIN; ... ROLLBACK;`:

1. conte quantas linhas dela existem em `materiais`, `trechos`, `flashcards` e `questoes`
   (uma única consulta, com subconsultas escalares);
2. apague a disciplina;
3. repita a contagem.

Perguntas: quantas linhas foram apagadas no total com um único `DELETE`? Quais FKs
participaram da cascata? Se uma FK no caminho fosse `RESTRICT`, o que mudaria?

**Minha resposta:**

```sql

```

### 1.3 Quando o índice é usável?

Na tabela `disciplinas`, rode `EXPLAIN` para cada filtro:

- (a) `WHERE usuario_id = <id>`
- (b) `WHERE usuario_id = <id> AND lower(nome) = 'grande'`
- (c) `WHERE usuario_id = <id> AND nome = 'Grande'`
- (d) `WHERE lower(nome) = 'grande'`

A tabela é pequena, então o planejador vai preferir *Seq Scan*. Para descobrir se o
índice `uq_disciplinas_usuario_nome` **poderia** ser usado, repita com
`SET enable_seqscan = off;`.

Perguntas: em quais casos o índice foi usado e com qual `Index Cond`? Explique (c) e (d)
usando a regra do prefixo à esquerda e o fato de o índice ser de **expressão**.

**Minha resposta:**

```sql

```

---

## Fase 2 — Busca vetorial, full-text e transações

Material de apoio: [`busca-semantica.md`](busca-semantica.md) e
[`experimentos/hnsw.md`](experimentos/hnsw.md). Rode o seed antes.

### 2.1 Por que o HNSW não foi usado?

Escreva uma consulta que encontre os 5 trechos mais parecidos com um trecho já existente
da disciplina "Grande", em três versões:

- (a) com um JOIN (ou CTE) que busca o vetor do trecho de referência;
- (b) com `disciplina_id = (SELECT id FROM disciplinas WHERE nome = 'Grande')`;
- (c) com o id da disciplina escrito direto na consulta.

Rode `EXPLAIN ANALYZE` nas três. Só uma usa `ix_trechos_embedding_hnsw`, e ela é bem mais
rápida.

Perguntas: qual delas, e por quê? Pense no que o planejador sabe sobre o valor do filtro
**na hora de planejar**, e no que o índice precisa ter do lado direito do `<=>`.

**Minha resposta:**

```sql

```

### 2.2 GIN contra ILIKE

Conte quantos trechos da disciplina "Grande" contêm as palavras "índice" e "árvore", de
três formas:

- (a) com `conteudo ILIKE '%indice%' AND conteudo ILIKE '%arvore%'`;
- (b) com `conteudo_tsv @@ to_tsquery('portugues_unaccent', 'indice & arvore')`;
- (c) a forma (b) dentro de `BEGIN; DROP INDEX ix_trechos_conteudo_tsv; ...; ROLLBACK;`.

Perguntas: por que (a) dá um número diferente de (b)? Compare os planos de (b) e (c)
(`Bitmap Index Scan` contra `Seq Scan`) e os tempos. Para mostrar o trecho com as palavras
destacadas, use `ts_headline`.

**Minha resposta:**

```sql

```

### 2.3 A corrida do compare-and-set

Abra **dois** psql (terminais A e B). Escolha um material e marque como pendente:
`UPDATE materiais SET status = 'pendente' WHERE id = <x>;`.

1. Em A e em B: `BEGIN;`
2. Em A: `UPDATE materiais SET status = 'processando' WHERE id = <x> AND status = 'pendente' RETURNING id;`
3. Em B: o mesmo comando. O que acontece?
4. Em A: `COMMIT;`. O que B mostra agora? Quantas linhas ele atualizou?
5. Repita trocando o passo 2/3 por `SELECT id FROM materiais WHERE id = <x> AND status = 'pendente' FOR UPDATE SKIP LOCKED;`.

Perguntas: por que B "esperou" no passo 3 e por que ele não processa o material duas
vezes? Qual a diferença de comportamento com `SKIP LOCKED`, e para que serviria numa
fila de processamento?

Bônus: tente inserir um trecho com um `disciplina_id` diferente do do material dele e
leia a mensagem da FK composta.

**Minha resposta:**

```sql

```

---

## Fase 3 — Geração com LLM, tabelas associativas e auditoria

Material de apoio: [`geracao-llm.md`](geracao-llm.md). Tabelas da fase: `geracoes`,
`alternativas`, `flashcard_trechos`, `questao_trechos`, além das colunas
`tentativas.alternativa_id` e `flashcards.geracao_id`/`questoes.geracao_id`. Para ter dados,
gere alguns flashcards e questões pela API e responda algumas questões (inclusive errando de
propósito).

### 3.1 Gastos com subtotais (ROLLUP) e o fuso horário

Escreva uma consulta em `geracoes` que mostre, por disciplina e por mês: número de
gerações, tokens de entrada, tokens de saída e custo total. Depois:

- (a) use `GROUP BY ROLLUP (...)` para ter também o subtotal de cada disciplina e o total
  geral, e use `GROUPING(...)` para rotular essas linhas ("subtotal", "total");
- (b) calcule também o custo médio por geração **bem-sucedida** e conte quantas falharam,
  numa linha só (dica: `FILTER (WHERE ...)`);
- (c) usando `geracao_id`, calcule o **custo por flashcard** de cada geração do tipo
  `flashcards`.

Perguntas: uma geração feita em 31/10 às 23h30 no horário de Brasília cai em que mês
com `date_trunc('month', criado_em)` e em que mês com
`date_trunc('month', criado_em AT TIME ZONE 'America/Sao_Paulo')`? Por quê? O que
acontece com as gerações cuja disciplina foi apagada (`disciplina_id` nulo) no ROLLUP?

**Minha resposta:**

```sql

```

### 3.2 Qual distrator mais engana?

Para cada questão de múltipla escolha com pelo menos 3 tentativas:

- (a) mostre a taxa de acerto;
- (b) mostre a alternativa **errada** mais escolhida e a porcentagem de tentativas que
  ela recebeu (dica: `count(*) OVER (PARTITION BY ...)` ou `DISTINCT ON`);
- (c) ordene das questões mais difíceis (menor taxa de acerto) para as mais fáceis.

Perguntas: por que isso é um `GROUP BY` simples com alternativas em tabela, e como você
faria a mesma análise se as alternativas estivessem num JSONB dentro de `questoes`
(dica: `jsonb_array_elements ... WITH ORDINALITY`)? Qual versão é mais fácil de indexar?

**Minha resposta:**

```sql

```

### 3.3 Constraint adiada, FK composta e N:N

Dentro de `BEGIN; ... ROLLBACK;`:

- (a) insira uma questão de múltipla escolha e duas alternativas, **nenhuma correta**.
  Em que momento o erro aparece: no INSERT ou no COMMIT? Rode
  `SET CONSTRAINTS ALL IMMEDIATE;` antes do COMMIT e observe a diferença;
- (b) tente marcar duas alternativas da mesma questão como corretas. Qual objeto do
  banco impede isso, e ele é adiado ou imediato?
- (c) tente registrar uma tentativa da questão X apontando para uma alternativa da
  questão Y. Leia a mensagem de erro e explique qual constraint barrou;
- (d) liste os flashcards cujos trechos de origem vêm de **mais de um material**
  (`flashcard_trechos` → `trechos`, com `HAVING count(DISTINCT ...)`).

Perguntas: por que a regra "pelo menos 2 alternativas e exatamente 1 correta" não cabe
num `CHECK`? Por que ela precisa ser **adiada** e não imediata? Em (d), por que uma
coluna `flashcards.trecho_id` não conseguiria guardar essa informação?

**Minha resposta:**

```sql

```

---

## Fase 4 — Repetição espaçada, estado × histórico, tempo e concorrência

Material de apoio: [`repeticao-espacada.md`](repeticao-espacada.md) e
[`experimentos/fila-do-dia.md`](experimentos/fila-do-dia.md). Rode antes o seed que simula
6 semanas de estudo:

```bash
docker compose exec backend python -m scripts.seed_revisoes
```

### 4.1 O que o histórico conta (e o estado não)

Usando só `historico_revisoes` (com JOINs até a disciplina do usuário
`estudante@estuda-ai.local`):

- (a) a taxa de acerto (nota ≥ 3) por disciplina e por **semana**, com a semana calculada no
  fuso de São Paulo (dica: `date_trunc('week', revisado_em AT TIME ZONE ...)` e
  `count(*) FILTER (WHERE ...)`);
- (b) o atraso médio, em dias, de cada revisão (`revisado_em - proxima_revisao_anterior`),
  por disciplina, separando revisões feitas em dia de semana das feitas no fim de semana;
- (c) o número de revisões por dia e a **média móvel de 7 dias** desse número
  (dica: `avg(...) OVER (ORDER BY dia ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)`).
  Os dias sem revisão aparecem? Como fazer aparecerem (dica: `generate_series`)?

Perguntas: qual dessas perguntas daria para responder só com a tabela `revisoes` (o estado)?
Por que o histórico guarda o estado *anterior* se ele é igual ao *novo* da linha de antes?

**Minha resposta:**

```sql

```

### 4.2 O estado é derivável do histórico?

- (a) para cada card, pegue a **última** linha do histórico de três jeitos: `DISTINCT ON`,
  `row_number() OVER (...)` e `LATERAL (... ORDER BY ... LIMIT 1)`. Compare os planos com
  `EXPLAIN ANALYZE`. Qual índice os três aproveitam?
- (b) compare essa última linha com `revisoes` (facilidade, intervalo, repetições, próxima
  revisão) e liste as divergências (deveria dar zero linhas; dica: `EXCEPT`). E a `versao`:
  ela bate com a contagem de linhas do histórico de cada card?
- (c) tente `UPDATE historico_revisoes SET nota = 5 WHERE ...`. Que objeto do banco impede, e
  por que um `REVOKE UPDATE` não teria o mesmo efeito para o usuário `estuda_ai`?
- (d) usando a função `sm2()` do banco, simule: "se todas as notas 3 de um card tivessem sido
  4, qual seria a facilidade final?" (dica: uma CTE recursiva que reaplica `sm2()` revisão a
  revisão, na ordem de `revisado_em`).

**Minha resposta:**

```sql

```

### 4.3 Tempo e concorrência na prática

**Parte A, fuso e horário de verão.** No psql:

```sql
SET TIME ZONE 'America/New_York';
SELECT timestamptz '2026-10-31 12:00' + interval '1 day',
       timestamptz '2026-10-31 12:00' + interval '24 hours';
```

- (a) por que os dois resultados diferem? O que acontece com a mesma conta em
  `SET TIME ZONE 'America/Sao_Paulo'`?
- (b) escreva a expressão do "fim de hoje" de um usuário em Tóquio e de um em São Paulo
  para o mesmo instante `timestamptz '2026-10-07 22:30-03'`. Em que data cai cada um?
- (c) por que `now()::date` numa sessão em UTC é um jeito errado de calcular "hoje" para um
  aluno brasileiro?

**Parte B, o duplo clique em dois psql.** Escolha um card do estudante e veja sua `versao`.
Nos terminais A e B, dentro de `BEGIN;`:

```sql
UPDATE revisoes SET repeticoes = repeticoes + 1, versao = versao + 1,
                    ultima_revisao_em = now(), proxima_revisao = now() + interval '1 day'
WHERE flashcard_id = <id> AND versao = <versão que você viu>
RETURNING versao;
```

- (d) o que B faz enquanto A não dá COMMIT? Quantas linhas B atualiza depois do COMMIT de A?
  Repita com `SELECT ... FOR UPDATE` seguido do `UPDATE` sem o `AND versao = ...` e compare.
- (e) por que a fila do usuário inteiro usa `CROSS JOIN LATERAL` e a de uma disciplina não?
  Rode `EXPLAIN ANALYZE` nas duas formas (JOIN simples e LATERAL) para o estudante e compare
  os planos.

**Minha resposta:**

```sql

```

---

## Fase 5 — SQL analítico (nível entrevista técnica)

Material de apoio: [`analytics.md`](analytics.md) e
[`experimentos/analytics.md`](experimentos/analytics.md). Use o seed com vários alunos:

```bash
docker compose exec backend python -m scripts.seed_revisoes --alunos 200
```

Estes são problemas clássicos de entrevista, adaptados às tabelas do projeto. Para cada um:
escreva a consulta, confira o resultado com um caso pequeno que você consegue calcular de
cabeça e, depois, rode `EXPLAIN ANALYZE` e diga qual é a parte mais cara do plano.

### 5.1 Sessões de estudo (gaps-and-islands em timestamps)

Uma **sessão** é uma sequência de revisões do mesmo aluno em que nenhum intervalo entre duas
revisões seguidas passa de 30 minutos. Para o estudante:

- (a) liste as sessões com início, fim, duração e número de revisões;
- (b) calcule a duração média e a mediana das sessões por dia da semana;
- (c) qual foi a sessão mais longa de cada aluno (dica: top 1 por grupo)?

Dica: `LAG(revisado_em)` marca onde começa uma sessão nova; uma **soma acumulada** dessas
marcas (`SUM(...) OVER (ORDER BY ...)`) numera as sessões. Por que a técnica do
`dia - row_number()` da Fase 5 não serve aqui?

**Minha resposta:**

```sql

```

### 5.2 Coorte de retenção

Agrupe os cards pela **semana em que foram criados** (a coorte). Para cada coorte, mostre a
taxa de acerto na 1ª, na 2ª e na 3ª revisão de cada card, em colunas lado a lado (uma linha
por coorte).

Dicas: `ROW_NUMBER() OVER (PARTITION BY flashcard_id ORDER BY revisado_em)` dá o número da
revisão; "pivotar" linhas em colunas sem `crosstab` se faz com agregação condicional
(`avg(...) FILTER (WHERE n = 1)`). As coortes mais recentes devem ter buracos na 3ª revisão:
mostre isso como `NULL`, não como 0. Por quê?

**Minha resposta:**

```sql

```

### 5.3 Mediana, percentil e a armadilha da janela

Usando `tentativas.tempo_ms`:

- (a) o tempo mediano de resposta por questão e por disciplina (`percentile_cont(0.5)`);
- (b) as questões cujo tempo mediano passa do **percentil 90** dos tempos medianos da sua
  disciplina;
- (c) divida os 200 alunos em quartis pela taxa de acerto geral (`NTILE(4)`) e mostre, por
  quartil, a taxa média e o número de dias estudados.

A armadilha de (b): `percentile_cont` **não** é uma função de janela
(`percentile_cont(...) OVER (...)` dá erro). Como contornar? E qual a diferença entre
`percentile_cont` e `percentile_disc`?

**Minha resposta:**

```sql

```

### 5.4 Recordes de sequência

Para cada aluno, liste os dias em que ele **bateu o próprio recorde** de sequência de estudo
(o tamanho da sequência atual naquele dia passou a ser o maior até então).

Dicas: primeiro as ilhas (gaps-and-islands); depois, para cada dia de uma ilha, o tamanho
parcial da sequência até ele (`row_number()` dentro da ilha); por fim, um
`MAX(...) OVER (ORDER BY dia ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)` para comparar
com o recorde **anterior**. Por que o quadro termina em `1 PRECEDING` e não em `CURRENT ROW`?

**Minha resposta:**

```sql

```

### 5.5 Divisão relacional

- (a) os alunos que estudaram em **todos** os 7 dias de uma semana escolhida;
- (b) os alunos que revisaram pelo menos um card de **todas** as suas disciplinas nos últimos
  7 dias.

Resolva (b) de dois jeitos: com `GROUP BY ... HAVING count(DISTINCT ...) = (subconsulta)` e com
`NOT EXISTS (... NOT EXISTS ...)` ("não existe disciplina do aluno sem revisão"). Compare os
planos. O que acontece com um aluno que **não tem** nenhuma disciplina, em cada versão?

**Minha resposta:**

```sql

```

### 5.6 Queda de ritmo

Para cada aluno e dia, compare o número de revisões com a **média dos 7 dias anteriores**
(sem incluir o próprio dia) e liste os dias com queda de mais de 50%.

- (a) escreva o quadro da janela que exclui o dia atual;
- (b) os dias sem estudo precisam entrar na média como 0. Faça isso de dois jeitos:
  densificando com `generate_series` (quadro `ROWS`) e sem densificar (quadro `RANGE` com
  `interval`). Os dois dão o mesmo resultado? Em que caso não dariam?
- (c) essa consulta deveria ler `mv_respostas_diarias` ou `vw_respostas`? Justifique com
  `EXPLAIN ANALYZE` para os 200 alunos.

**Minha resposta:**

```sql

```

### 5.7 Duplicatas e um DELETE seguro

Simule um bug de duplo envio (dentro de `BEGIN; ... ROLLBACK;`): copie 50 tentativas do
estudante com `respondida_em` 2 segundos depois da original. Depois:

- (a) encontre os pares duplicados: mesma questão, mesma alternativa, menos de 5 s de
  diferença;
- (b) apague as duplicatas mantendo a **primeira** de cada grupo, de dois jeitos: com
  `ROW_NUMBER()` numa CTE (`DELETE ... WHERE id IN (SELECT ...)`) e com
  `DELETE ... USING` (autojunção);
- (c) por que `DELETE` de linhas duplicadas sem chave primária seria muito mais difícil, e
  como o Postgres permitiria fazê-lo mesmo assim (dica: `ctid`)? Por que não confiar em
  `ctid` numa aplicação?

**Minha resposta:**

```sql

```

## Fase 6 — Segurança, privilégios, operação e backup

Conceitos em [`seguranca.md`](seguranca.md), [`frontend.md`](frontend.md) e
[`deploy.md`](deploy.md). Faça no banco de **desenvolvimento** (nunca no de produção),
conectado como dono (`docker compose exec db psql -U estuda_ai -d estuda_ai`) e trocando
de papel com `SET ROLE estuda_ai_app;` / `RESET ROLE;` quando o exercício pedir.

### 6.1 O que o papel da aplicação consegue fazer

Com `SET ROLE estuda_ai_app;`, tente (e explique cada resultado):

- (a) `UPDATE geracoes SET custo_usd = 0;` e `DELETE FROM historico_revisoes;`;
- (b) `DELETE FROM disciplinas WHERE id = <uma sua>;` dentro de `BEGIN; ... ROLLBACK;`. O
  histórico de revisões dos cards dela some junto, mesmo o papel não tendo `DELETE` em
  `historico_revisoes`. Por quê? Mostre no catálogo (`pg_constraint`, coluna `confdeltype`)
  qual FK faz isso;
- (c) escreva UMA consulta no catálogo que liste, para cada tabela do schema `public`,
  quais privilégios o `estuda_ai_app` tem, no formato `tabela | SELECT, INSERT, ...`
  (dica: `has_table_privilege` + `string_agg`). Compare com `\dp` no psql.

**Minha resposta:**

```sql

```

### 6.2 SECURITY DEFINER e search_path

- (a) Ainda como `estuda_ai_app`, rode `REFRESH MATERIALIZED VIEW mv_respostas_diarias;` e
  depois `SELECT * FROM atualizar_mv_respostas_diarias();`. Por que um falha e o outro não?
- (b) Crie (como dono, num banco de teste) uma função `SECURITY DEFINER` **sem**
  `SET search_path` que faça `SELECT count(*) FROM geracoes`. Mostre como um papel que pode
  criar tabelas temporárias consegue fazer a função ler outra tabela `geracoes` (dica:
  `CREATE TEMP TABLE geracoes ...` e a posição de `pg_temp` no search_path). Depois
  conserte com `SET search_path = pg_catalog, public, pg_temp`;
- (c) por que, além do `search_path`, é preciso `REVOKE EXECUTE ... FROM PUBLIC`?

**Minha resposta:**

```sql

```

### 6.3 Rate limit: janela fixa x janela deslizante

A tabela `limites_taxa` implementa janela fixa com `date_bin`.

- (a) Mostre, com `INSERT`s em horários escolhidos, como um cliente consegue fazer quase
  o **dobro** do limite em poucos segundos ao redor da virada da janela;
- (b) escreva uma janela **deslizante** em SQL: uma tabela `tentativas_login(chave,
  momento)` e uma consulta que conte as tentativas nos últimos 15 minutos para uma chave.
  Que índice ela precisa? Como você limparia as linhas velhas?
- (c) compare as duas em custo de escrita, tamanho da tabela e precisão. Por que a
  aplicação aceitou a janela fixa?

**Minha resposta:**

```sql

```

### 6.4 UPSERT e concorrência

- (a) Em duas sessões do psql, com `BEGIN;` em ambas, rode o mesmo
  `INSERT ... ON CONFLICT (chave, janela_inicio) DO UPDATE SET contagem = contagem + 1
  RETURNING contagem` na mesma chave. O que a segunda sessão faz até a primeira dar
  `COMMIT`? Qual o valor final? Use `pg_locks` para ver a espera;
- (b) reescreva o contador como "`SELECT` e depois `UPDATE` ou `INSERT`" (sem
  `ON CONFLICT`) e mostre, com as duas sessões, a atualização perdida (ou o erro de
  chave duplicada);
- (c) por que a tabela pode ser `UNLOGGED` e a `geracoes` (a cota diária) não pode?

**Minha resposta:**

```sql

```

### 6.5 Backup, restore e o que um dump não leva

- (a) Rode `deploy/backup.sh` (ou um `pg_dump --format=custom` à mão) e liste o conteúdo
  com `pg_restore --list`. Em que ordem aparecem tabelas, dados, índices e constraints? Por
  que essa ordem acelera o restore?
- (b) Restaure o dump num banco **novo** (`CREATE DATABASE restore_teste;`) com
  `pg_restore -d restore_teste`. Que erros aparecem se o papel `estuda_ai_app` não existir
  no servidor? Por que o `pg_dump` não leva papéis (e qual ferramenta leva)?
- (c) Confira que o restore está inteiro: compare a contagem de linhas de cada tabela entre
  os dois bancos com uma consulta só (dica: `pg_stat_user_tables.n_live_tup` engana; por quê?
  Use `count(*)`).
- (d) O backup diário perde até 24 h de dados se a VM sumir às 03:59. Explique como o
  arquivamento contínuo do WAL (`archive_mode`, `pg_basebackup`) reduziria isso para
  minutos, e o que é PITR (*point-in-time recovery*).

**Minha resposta:**

```sql

```

### 6.6 SQL injection e o operador LIKE

- (a) Monte, com `PREPARE busca(text) AS SELECT ... WHERE email ILIKE $1;`, um exemplo em
  que o valor vem como parâmetro (sem injection nenhuma) e mesmo assim casa com todas as
  contas. Por que bind parameter não impede isso?
- (b) Escreva uma busca por **prefixo** de título de material que seja segura com entrada
  do usuário: escape `%`, `_` e `\` antes do `LIKE` (função `replace` ou `ESCAPE`) e mostre
  que `50%` encontra só títulos que começam com "50%";
- (c) que índice faz `WHERE lower(titulo) LIKE 'abc%'` usar índice no Postgres
  (dica: `text_pattern_ops` ou collation "C")? Prove com `EXPLAIN`.

**Minha resposta:**

```sql

```

## Fase 7 — App desktop, sessões offline e idempotência

A sessão 2 desta fase grava no Postgres sessões de estudo que podem chegar **mais de uma
vez**: o app desktop guarda tudo num SQLite local quando a internet cai e reenvia depois.
Estes exercícios preparam o terreno; os próximos entram junto com as sessões 2 a 4.

### 7.1 Reenvio, duplicata e `ON CONFLICT`

Crie uma tabela de rascunho (fora das migrations, num banco de teste):
`CREATE TABLE envio (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, usuario_id bigint
NOT NULL, chave uuid NOT NULL, minutos int NOT NULL);`

- (a) Simule o problema: o app manda "sessão de 50 minutos", a resposta se perde (timeout)
  e o app manda de novo. Rode o mesmo `INSERT` duas vezes. Quantas sessões existem, e
  quanto vale `SUM(minutos)` no gráfico de horas de foco?
- (b) Acrescente `UNIQUE (usuario_id, chave)` e reescreva o envio com
  `INSERT ... ON CONFLICT (usuario_id, chave) DO NOTHING RETURNING id`. O que o
  `RETURNING` devolve na segunda vez? Como o servidor responde "já recebi" ao app
  nesse caso, sem um `SELECT` antes (lembre da regra "nada de SELECT para checar antes do
  INSERT" do CLAUDE.md: por que ela existe)?
- (c) Por que a chave é **gerada no app** (um UUID criado quando a sessão começa) e não um
  `id` do banco? O que daria errado se fosse `(usuario_id, iniciada_em)`?
- (d) Uma sessão é enviada "em andamento" e depois "concluída". Escreva um
  `ON CONFLICT ... DO UPDATE` que aceite a mudança de status, mas nunca volte de
  "concluída" para "em andamento" se os envios chegarem fora de ordem (dica: `WHERE` no
  `DO UPDATE` e a pseudo-tabela `EXCLUDED`).
- (e) A unicidade é `(usuario_id, chave)` e não só `(chave)`. Que ataque ou bug a versão
  com `usuario_id` evita?

**Minha resposta:**

```sql

```

### 7.2 Colunas geradas e o que não cabe nelas

- (a) `\d+ sessoes_estudo`: como o `psql` mostra `duracao_planejada_s` e `duracao_real_s`?
  Tente `UPDATE sessoes_estudo SET duracao_real_s = 0 WHERE id = ...` (dentro de `BEGIN;
  ... ROLLBACK;`). Qual é o erro?
- (b) Por que `foco_efetivo_s` **não** pode ser uma coluna gerada? (Dica: de onde vêm as
  pausas.) Que alternativas existiriam (view, trigger, coluna gravada pelo app) e que
  risco cada uma traz?
- (c) Compare `SELECT GREATEST(NULL, 0)` com `SELECT NULL > 0`. Por que a view usa
  `CASE WHEN duracao_real_s IS NOT NULL THEN GREATEST(...) END`, e o que `avg()` faz com
  `NULL` e com `0`?

**Minha resposta:**

```sql

```

### 7.3 `ON CONFLICT`, `xmax` e o snapshot do comando

- (a) Num `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING (xmax = 0)`, o que `xmax`
  vale numa linha recém-inserida e numa linha atualizada? Prove com duas execuções.
- (b) Abra **duas** sessões do `psql`. Na 1ª: `BEGIN;` e insira uma sessão com uma chave
  fixa (sem `COMMIT`). Na 2ª: o mesmo `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE` com
  o `UNION ALL SELECT` de reserva de `SQL_SESSAO`. O que a 2ª faz enquanto a 1ª não
  confirma? Dê `COMMIT` na 1ª: o que a 2ª devolve? Por que um **novo** `SELECT` na 2ª acha
  a linha?
- (c) Em que nível de isolamento a releitura num comando novo NÃO resolveria (dica:
  `REPEATABLE READ`)?

**Minha resposta:**

```sql

```

### 7.4 `LATERAL` top-1 e o plano

- (a) Reescreva a consulta do acerto depois de cada método **sem** `LATERAL` (dica: janela
  `row_number() OVER (PARTITION BY ... ORDER BY terminada_em DESC)` sobre um JOIN por
  intervalo). Compare os dois `EXPLAIN ANALYZE` com o seed de 200 alunos.
- (b) Apague o índice `ix_sessoes_estudo_usuario_terminada_em` (dentro de `BEGIN; ...
  ROLLBACK;`) e rode o `EXPLAIN` de novo. Que plano aparece e quantas linhas cada
  execução da subconsulta lê?
- (c) Por que o índice é **parcial** (`WHERE terminada_em IS NOT NULL`)? A consulta precisa
  repetir essa condição para o planejador poder usá-lo?

**Minha resposta:**

```sql

```
