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
- (b) um flashcard com `facilidade = 1.29`;
- (c) um flashcard com `facilidade` omitida;
- (d) um trecho com `pagina = NULL` e outro com `pagina = 0`;
- (e) uma revisão com `proxima_revisao` anterior a `revisado_em`.

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
