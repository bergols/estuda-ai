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
