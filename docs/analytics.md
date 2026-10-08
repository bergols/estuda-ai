# Analytics com SQL — estuda-ai

A Fase 5 entrega os dados de desempenho prontos para plotar (os gráficos são da Fase 6).
Toda consulta é **SQL explícito** em `backend/app/servicos/analytics.py`, comentada linha a
linha. Este documento explica o conceito que cada uma ensina e o que o `EXPLAIN ANALYZE`
mostrou. Os números de performance vêm de
[`experimentos/analytics.md`](experimentos/analytics.md) (200 alunos simulados, 435 mil
revisões, gerado por script).

| Rota (`GET`, salvo indicação) | O que devolve | Lê de | Conceito principal |
|---|---|---|---|
| `/analytics/acerto-semanal?por=disciplina` | taxa de acerto por semana, disciplina e fonte | MV | `date_trunc`, `GROUPING SETS`, densificar série |
| `/analytics/acerto-semanal?por=material` | idem, por material | ao vivo | N:N sem dupla contagem |
| `/analytics/evolucao/diaria` | taxa por dia + média móvel de 7 dias | MV | window frame `ROWS`, razão das somas |
| `/analytics/evolucao/semanal` | taxa por semana × semana anterior | MV | `LAG` |
| `/analytics/cards-dificeis` | top N cards por disciplina | ao vivo | `DENSE_RANK` + `PARTITION BY` |
| `/analytics/sequencia` | sequência atual e maior | ao vivo | gaps-and-islands, CTEs encadeadas |
| `/analytics/previsao` | cards vencendo nos próximos N dias | ao vivo | `generate_series` |
| `/analytics/calendario` | revisões por dia no último ano + nível 0–4 | MV | `generate_series`, `percentile_cont` |
| `/analytics/custos` | gasto de IA por mês e tipo, acumulado | ao vivo | `SUM() OVER`, quadro `RANGE` |
| `POST /analytics/atualizar` | refresh da materialized view | — | `REFRESH ... CONCURRENTLY` |

Filtros: `disciplina_id` em todas, `de`/`ate` (datas **no fuso do usuário**) onde há
período. Os dados do seed (`scripts/seed_revisoes.py`) ficam em
`estudante@estuda-ai.local`: 480 cards, 1.961 revisões, 142 tentativas e 112 gerações de IA
em 6 semanas.

---

## 0. VIEW × MATERIALIZED VIEW

Duas views novas (migration `views_de_analytics`):

```sql
-- VIEW: revisões e tentativas num formato só, com o dia local já calculado
CREATE VIEW vw_respostas AS
  SELECT d.usuario_id, f.disciplina_id, 'revisao' AS fonte, h.flashcard_id AS item_id,
         h.nota >= 3 AS acertou, h.nota, h.revisado_em AS respondido_em,
         (h.revisado_em AT TIME ZONE u.fuso_horario)::date AS dia
  FROM historico_revisoes h JOIN flashcards f ... JOIN disciplinas d ... JOIN usuarios u ...
  UNION ALL
  SELECT ..., 'questao', t.questao_id, t.correta, NULL, t.respondida_em, ... FROM tentativas t ...;

-- MATERIALIZED VIEW: o resultado agregado, gravado em disco
CREATE MATERIALIZED VIEW mv_respostas_diarias AS
  SELECT usuario_id, disciplina_id, dia, fonte,
         count(*) AS respostas, count(*) FILTER (WHERE acertou) AS acertos
  FROM vw_respostas GROUP BY usuario_id, disciplina_id, dia, fonte;
CREATE UNIQUE INDEX uq_mv_respostas_diarias ON mv_respostas_diarias (usuario_id, disciplina_id, dia, fonte);
```

| | VIEW | MATERIALIZED VIEW |
|---|---|---|
| Guarda dados? | não: é uma consulta com nome | sim: o resultado, como uma tabela |
| A cada SELECT | roda a consulta de novo | lê o que está gravado |
| Dado | sempre atual | **parado no último `REFRESH`** |
| Índices | não tem (usa os das tabelas) | tem os seus |
| Use para | reaproveitar e esconder uma consulta (aqui: o `UNION ALL` + o dia local) | uma agregação pesada lida muitas vezes, que tolera atraso |

Detalhes que ensinam algo:
- **`UNION ALL`, não `UNION`**: `UNION` remove linhas duplicadas, o que custa uma ordenação
  ou um hash. As duas partes nunca coincidem (a `fonte` difere), então seria trabalho à toa.
- O **dia local** é calculado na view, uma vez, em vez de repetido em toda consulta. Se o
  usuário mudar de fuso, a MV só reflete isso depois do próximo refresh.

### O problema do dado desatualizado

Uma revisão feita agora **não aparece** nas rotas que leem a MV até o próximo refresh
(`test_materialized_view_so_mostra_o_novo_depois_do_refresh`). Por isso essas rotas devolvem
`atualizado_em`, e o cliente pode mostrar "dados de 10 minutos atrás" ou pedir um refresh. A
divisão escolhida:
- **na MV**: o que é histórico e agregado (acerto semanal, evolução, calendário). Atraso de
  minutos não muda a leitura de 12 semanas;
- **ao vivo**: o que precisa refletir o que **acabou** de acontecer (sequência de hoje, previsão
  da fila, ranking, custos).

### Refresh comum × CONCURRENTLY

| | `REFRESH MATERIALIZED VIEW` | `REFRESH ... CONCURRENTLY` |
|---|---|---|
| Como | recalcula e troca o conteúdo inteiro | recalcula, compara com o atual e aplica só as diferenças |
| Leitores durante o refresh | **bloqueados** (lock `ACCESS EXCLUSIVE`) | continuam lendo a versão anterior |
| Exige | — | um índice **único**, sobre colunas, sem `WHERE` (para casar linha velha com linha nova) |
| Medido (200 alunos) | **168 ms** | **411 ms** |

A API usa `CONCURRENTLY` (`POST /analytics/atualizar`), porque há leitores. O seed usa o
comum, porque ninguém está lendo durante a carga e ele é 2,5× mais rápido.

O horário do refresh fica na tabela `atualizacoes_mv`, e **não** como `now()` dentro da MV:
com `now()` em toda linha, todas mudariam a cada refresh e o `CONCURRENTLY` teria de reescrever
a MV inteira, perdendo a vantagem de aplicar só as diferenças.

Estratégias de refresh que ficaram de fora: a cada revisão (sem atraso, mas recalcula tudo a
cada nota e anula o ganho da MV) e agendado com `pg_cron` (exige uma imagem do Postgres com a
extensão).

---

## 1. Taxa de acerto por semana

```sql
WITH agregado AS (
    SELECT date_trunc('week', m.dia)::date AS semana, m.disciplina_id,
           CASE WHEN GROUPING(m.fonte) = 1 THEN 'total' ELSE m.fonte END AS fonte,
           sum(m.respostas)::int AS respostas, sum(m.acertos)::int AS acertos
    FROM mv_respostas_diarias m
    WHERE m.usuario_id = :usuario_id AND m.dia BETWEEN :de AND :ate
    GROUP BY GROUPING SETS (
        (date_trunc('week', m.dia), m.disciplina_id, m.fonte),   -- por fonte
        (date_trunc('week', m.dia), m.disciplina_id)             -- revisões + questões
    )
),
semanas AS (SELECT generate_series(date_trunc('week', :de), :ate, interval '1 week')::date AS semana),
grade AS (SELECT ... FROM semanas CROSS JOIN disciplinas CROSS JOIN (VALUES ('revisao'), ('questao'), ('total')) ...)
SELECT g.*, coalesce(a.respostas, 0), round(a.acertos::numeric / nullif(a.respostas, 0), 4) AS taxa
FROM grade g LEFT JOIN agregado a USING (semana, disciplina_id, fonte);
```

- **`date_trunc('week', dia)`** leva cada dia para a segunda-feira da sua semana (ISO). Com
  `dia` já local, a semana é a do aluno.
- **`GROUPING SETS`** faz vários `GROUP BY` numa passada. `GROUPING(col)` é 1 nas linhas em
  que `col` foi "agregada fora", e é assim que a linha do total é rotulada. `ROLLUP (a, b)`
  é um atalho para `GROUPING SETS ((a, b), (a), ())`, e `CUBE` gera todas as combinações.
- **Densificar a série**: `GROUP BY` só produz as semanas que **têm** dados. Um gráfico
  precisa da semana vazia como vazia. O produto cartesiano (`generate_series` × disciplinas ×
  fontes) é a "grade" de tudo que deve aparecer, e o `LEFT JOIN` completa com 0.
- **Taxa = razão das somas** e `nullif(x, 0)` para não dividir por zero (`x / NULL = NULL`).
- `sum()` de `bigint` devolve `numeric` (para não estourar); `::int` volta a inteiro.

### Por material (N:N)

Material não está na MV: a ligação card → material passa por `flashcard_trechos → trechos`,
uma relação N:N. Dois cuidados:
1. **Dupla contagem**: um card ligado a 2 trechos da **mesma** apostila apareceria 2 vezes no
   JOIN. `SELECT DISTINCT (item, material)` deixa um par por material
   (`test_acerto_por_material_nao_conta_duas_vezes_o_mesmo_material`). Um card ligado a
   apostilas **diferentes** conta nas duas, porque a resposta é sobre as duas; logo a soma por
   material passa do total de respostas, e isso é esperado.
2. **Performance**: a primeira versão fazia o `DISTINCT` sobre as associações de **todos** os
   alunos (115 mil linhas, ~63 mil páginas) e só depois filtrava um. Filtrando pelas
   disciplinas do aluno **antes**: **77 ms → 5,3 ms**. "Filtre o mais cedo possível" é a regra
   que o planejador não consegue aplicar sozinho através de um `DISTINCT`.

---

## 2. Evolução: média móvel e comparação semanal

### Média móvel de 7 dias (window frame)

```sql
sum(respostas) OVER (ORDER BY dia ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS respostas_7d,
sum(acertos)   OVER (ORDER BY dia ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS acertos_7d
-- taxa_media_7d = acertos_7d / respostas_7d
```

- O **quadro** (*frame*) define quais linhas a janela enxerga. `ROWS BETWEEN 6 PRECEDING AND
  CURRENT ROW` = a linha atual e as 6 anteriores. Só é "7 dias" se houver **uma linha por
  dia**, e por isso a série é densificada com `generate_series`. Alternativa sem densificar:
  `RANGE BETWEEN interval '6 days' PRECEDING AND CURRENT ROW`, que olha a **distância** no
  valor de `dia`, e não o número de linhas.
- **Razão das somas, não média das taxas.** Dia 1: 10 de 10 (100%). Dia 2: nada. Dia 3: 0 de 2
  (0%). A média das taxas dá 50%; a razão das somas dá 10/12 = **83%**, e é a correta, porque
  o dia com 10 respostas tem de pesar mais (`test_media_movel_e_razao_das_somas...`; com a
  fórmula errada o teste falha, isso foi conferido).
- **Janelas só enxergam o que passou pelo `WHERE`.** Para o primeiro dia pedido já ter 7 dias
  na janela, a série começa 6 dias **antes** de `:de`, e o corte `dia >= :de` acontece numa
  consulta de **fora**, depois da janela.

### Semana × semana anterior (LAG)

```sql
lag(taxa) OVER (ORDER BY semana) AS taxa_semana_anterior,
round(100 * (taxa - lag(taxa) OVER (ORDER BY semana)), 2) AS variacao_pp
```

- `LAG(x)` é o valor de `x` na linha anterior (`LEAD`, na seguinte).
- **Densificar de novo**: sem a semana vazia, o `LAG` da semana seguinte compararia com
  **duas** semanas atrás, sem avisar (`test_semana_vazia_no_meio_aparece_e_o_lag_compara_com_ela`).
- `variacao_pp` está em **pontos percentuais** (62% → 70% = +8 pp), diferente de variação
  percentual (+12,9%).

---

## 3. Cards mais difíceis: ranking por grupo

```sql
WITH estatisticas AS (... count(*) FILTER (WHERE h.nota < 3) AS erros, r.facilidade ...),
ranqueado AS (
    SELECT *, dense_rank() OVER (PARTITION BY disciplina_id ORDER BY erros DESC, facilidade ASC) AS posicao
    FROM estatisticas
)
SELECT ... FROM ranqueado WHERE posicao <= :limite;
```

| Função | Cards com erros 3, 2, 2, 1 | Quando usar |
|---|---|---|
| `ROW_NUMBER()` | 1, 2, 3, 4 | numerar; desempate arbitrário (ou por uma coluna a mais) |
| `RANK()` | 1, 2, 2, **4** | "posição de corrida": empate pula a seguinte |
| `DENSE_RANK()` | 1, 2, 2, **3** | "níveis": empate não pula |

- `PARTITION BY disciplina_id` reinicia a numeração em cada disciplina: é o **top N por
  grupo**, que `GROUP BY ... ORDER BY ... LIMIT` não resolve.
- Com `DENSE_RANK`, o "top 2" traz **3** cards se dois empatarem em 2º. Os empatados são
  igualmente difíceis, e cortar um deles seria arbitrário (`test_cards_dificeis_com_empate...`).
- **Janela não vai no `WHERE`**: as funções de janela são calculadas **depois** do `WHERE`.
  O filtro `posicao <= :limite` fica numa consulta de fora (o Postgres não tem o `QUALIFY` de
  outros bancos).
- A rota devolve `posicao` (dense) e `posicao_rank` (rank), para comparar.

---

## 4. Sequência de estudo: gaps-and-islands

```sql
WITH dias_estudados AS (SELECT DISTINCT dia FROM vw_respostas WHERE usuario_id = :u AND fonte = 'revisao'),
ilhas AS (SELECT dia, dia - CAST(row_number() OVER (ORDER BY dia) AS integer) AS ilha FROM dias_estudados),
sequencias AS (SELECT min(dia) AS inicio, max(dia) AS fim, count(*) AS dias FROM ilhas GROUP BY ilha),
atual AS (SELECT * FROM sequencias, hoje WHERE fim >= hoje.dia - 1),
maior AS (SELECT * FROM sequencias ORDER BY dias DESC, fim DESC LIMIT 1)
SELECT ...;
```

Em dias consecutivos, `dia` e `row_number()` sobem juntos (+1, +1...), então `dia −
row_number()` fica **constante** dentro de cada sequência e muda quando há um buraco:

| dia | row_number | dia − row_number | |
|---|---:|---|---|
| 01/10 | 1 | 30/09 | ilha A |
| 02/10 | 2 | 30/09 | ilha A |
| 03/10 | 3 | 30/09 | ilha A |
| 06/10 | 4 | 02/10 | ilha B (buraco em 04 e 05) |

Agrupar pela diferença dá uma linha por sequência. A técnica serve para qualquer "período
contínuo": sessões de estudo, dias seguidos de login, faixas de números sem buraco.

- **Sequência atual** = a ilha que termina **hoje ou ontem**. Se o aluno ainda não estudou
  hoje, a sequência continua viva até o fim do dia (`test_sequencia_continua_viva...`).
- **Fuso**: o `dia` vem do fuso do usuário. 23h30 de 05/10 e 00h30 de 06/10 em São Paulo são
  dois dias locais; em UTC seriam o mesmo dia (`test_dia_da_sequencia_e_o_dia_local...`).
- **Ao vivo**, porque "estudei agora" tem de contar na hora.

---

## 5. Previsão de carga e 6. calendário: `generate_series`

```sql
WITH dias AS (SELECT generate_series(:hoje, :hoje + 29, interval '1 day')::date AS dia),
vencimentos AS (
    SELECT greatest((proxima_revisao AT TIME ZONE fuso)::date, :hoje) AS dia, count(*) AS cards ...
    GROUP BY 1
)
SELECT d.dia, coalesce(v.cards, 0) FROM dias d LEFT JOIN vencimentos v USING (dia);
```

- `GROUP BY` só produz os dias em que **algo** vence. `generate_series` cria todos os dias e o
  `LEFT JOIN` completa com 0. Sem isso, o gráfico "pularia" o dia vazio.
- Atrasados entram em **hoje** (`greatest(..., hoje)`), porque é quando serão revistos; e
  ficam também na coluna `atrasados`.
- É uma previsão pelo estado **atual**: cada revisão reagenda o card, então os dias distantes
  mudam.

O **calendário** (estilo GitHub) usa o mesmo padrão para 365 dias e calcula o **nível** de
cor (0 a 4) pelos quartis dos dias com atividade:

```sql
cortes AS (
    SELECT percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY revisoes) AS q
    FROM serie WHERE revisoes > 0
)
-- nivel: 0 se zero; 1 se <= q[1]; 2 se <= q[2]; 3 se <= q[3]; senão 4
```

`percentile_cont` é um **ordered-set aggregate**: a ordem dos valores
(`WITHIN GROUP (ORDER BY ...)`) faz parte do cálculo. Passar um `ARRAY` de frações devolve
vários percentis numa passada. (`percentile_cont` interpola; `percentile_disc` devolve um valor
que existe na lista.) O nível é **relativo ao próprio aluno**.

Um efeito colateral útil: a primeira versão do seed tinha um limite fixo de 80 revisões por
dia, quase todos os dias batiam nele, e os três quartis ficavam em 80, então todo dia caía no
nível 1. A consulta estava certa; o dado é que era pouco realista. O seed passou a sortear o
limite diário.

---

## 7. Custo de IA acumulado: `SUM() OVER` e o quadro `RANGE`

```sql
WITH por_mes AS (SELECT date_trunc('month', criado_em AT TIME ZONE fuso)::date AS mes, tipo,
                        sum(custo_usd) AS custo_usd ... GROUP BY 1, 2)
SELECT mes, tipo, custo_usd,
       sum(custo_usd) OVER (PARTITION BY tipo ORDER BY mes) AS acumulado_tipo,
       sum(custo_usd) OVER (ORDER BY mes)                   AS acumulado_total
FROM por_mes;
```

- Com `ORDER BY` e sem quadro explícito, o padrão é
  `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`. Em `RANGE`, as linhas com o **mesmo
  valor** de ordenação (o mesmo mês, para outros tipos) são **pares** e entram juntas. Então
  todas as linhas de agosto mostram o acumulado até o fim de agosto. Com `ROWS`, cada linha
  somaria só até ela mesma, e o resultado dependeria da ordem dos tipos dentro do mês. O
  teste `test_custos_por_mes_e_tipo_com_acumulados` fixa os valores.
- Sem a CTE, a mesma coisa sai num SELECT só: `sum(sum(custo_usd)) OVER (ORDER BY mes)`. O
  `sum()` de dentro é o agregado do `GROUP BY`; o de fora, a janela.
- O mês é o do **fuso do usuário**: 31/08 às 23h30 em São Paulo é setembro em UTC e conta em
  agosto.

---

## 8. CTEs: quando ajudam e quando atrapalham

**Ajudam a ler.** A sequência de estudo é cinco passos com nome (`dias_estudados → ilhas →
sequencias → atual → maior`). A mesma lógica em subconsultas aninhadas teria de ser lida de
dentro para fora.

**Como o Postgres executa uma CTE** (desde a versão 12):
- referenciada **uma vez**, sem efeitos colaterais: é **incorporada** (*inlined*) à consulta,
  como uma subconsulta. O planejador otimiza tudo junto;
- referenciada **mais de uma vez**: é **materializada** (calculada uma vez, guardada, lida
  várias vezes);
- você força com `AS MATERIALIZED` ou `AS NOT MATERIALIZED`.

**Atrapalham quando o filtro fica fora delas e elas são materializadas.** Medido:

```sql
WITH respostas AS MATERIALIZED (SELECT * FROM vw_respostas)   -- todos os alunos!
SELECT dia, count(*) FROM respostas WHERE usuario_id = :u GROUP BY dia;
```

| CTE | Mediana |
|---|---:|
| `NOT MATERIALIZED` (o padrão, referenciada uma vez) | **1,47 ms** |
| `MATERIALIZED` | **332 ms** |

Materializada, a CTE é uma **barreira de otimização**: ela calcula as respostas de **todos** os
200 alunos antes de o `WHERE usuario_id = ...` de fora ser aplicado. Antes do Postgres 12,
**toda** CTE se comportava assim, e daí vem a fama de "CTE é lenta".

**Quando `MATERIALIZED` ajuda:** quando a CTE é cara, usada várias vezes, e você quer garantir
que rode uma vez só; ou quando contém uma função volátil (`random()`) que deve ser avaliada uma
única vez. CTEs **recursivas** (`WITH RECURSIVE`) e as que modificam dados
(`WITH x AS (DELETE ... RETURNING ...)`) são sempre materializadas.

Na sequência de estudo, `dias_estudados` é referenciada duas vezes (por `ilhas` e na coluna
`estudou_hoje`), então é materializada, mas já vem filtrada pelo usuário: o custo é pequeno.

---

## 9. Performance: o que o EXPLAIN mostrou

| Caso | Antes | Depois | O que mudou |
|---|---:|---:|---|
| Evolução diária (30 dias) | 1,79 ms ao vivo | **0,12 ms** MV | agregação pronta |
| Calendário (365 dias) | 1,86 ms ao vivo | **0,28 ms** MV | idem |
| Acerto por material | 77 ms | **5,3 ms** | filtrar pelo aluno antes do `DISTINCT` |
| Filtro de período | 0,82 ms | **0,46 ms** | condição sargable de verdade (abaixo) |
| Ranking de cards | 1,70 ms | **1,32 ms** | `INCLUDE (nota)`: Index Only Scan |
| CTE com filtro fora | 332 ms | **1,47 ms** | não materializar |

Em valores absolutos, quase tudo já era rápido: cada aluno tem ~2 mil revisões. A MV e os
índices importam porque o custo **ao vivo** cresce com o histórico de cada aluno, e o da MV
cresce com o número de dias.

### Sargable: duas condições, não uma

Uma condição é *sargable* (*Search ARGument ABLE*) quando um índice pode usá-la para **achar**
as linhas (`Index Cond`), e não só para **descartar** depois de ler (`Filter`). O filtro de
período ensinou que são duas regras:

1. **Não aplicar função na coluna.** `(revisado_em AT TIME ZONE fuso)::date >= :de` é
   avaliado linha a linha. O certo é converter os **limites**:
   `revisado_em >= (:de::timestamp AT TIME ZONE fuso)`.
2. **O limite precisa ser conhecido antes da varredura.** Com o fuso vindo de
   `u.fuso_horario` (uma coluna do JOIN), o limite só existe linha a linha, e a condição
   continuou `Join Filter`: 1.765 linhas lidas e descartadas. Com o fuso numa **subconsulta
   escalar**, ela vira um `InitPlan` (calculado uma vez, antes) e a condição entra no índice:

```
Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h
  Index Cond: ((flashcard_id = f.id) AND (revisado_em >= (... AT TIME ZONE $0))
                                     AND (revisado_em < (... AT TIME ZONE $1)))
  Heap Fetches: 0
```

| Versão | Mediana | Como o período é aplicado |
|---|---:|---|
| `(coluna AT TIME ZONE ...)::date BETWEEN` | 0,82 ms | `Join Filter` |
| coluna crua, limite de `u.fuso_horario` | 0,73 ms | `Join Filter` |
| coluna crua, limite em subconsulta (InitPlan) | **0,46 ms** | **`Index Cond`** |

### `INCLUDE` e o index-only scan

```sql
CREATE INDEX ... ON historico_revisoes (flashcard_id, revisado_em) INCLUDE (nota);
```

`INCLUDE` guarda `nota` nas folhas do índice sem fazer dela parte da chave (não entra na
ordenação nem na busca). Uma consulta que só precisa de `flashcard_id`, `revisado_em` e `nota`
é respondida **só pelo índice** (*Index Only Scan*), sem ler a tabela. Desde que o **mapa de
visibilidade** esteja em dia: o índice não sabe se a versão da linha é visível para a sua
transação (MVCC), e só pula a tabela nas páginas marcadas como "tudo visível" pelo `VACUUM`. O
`Heap Fetches: 0` no plano mostra isso. O experimento roda `VACUUM` antes.

- Ranking de cards: `Index Scan` → `Index Only Scan`, ~35% menos páginas, 1,70 → 1,32 ms.
- Sequência: sem mudança, porque ela não precisa de `nota` e **já** fazia index-only scan.
- **Adotado**: o índice novo **substitui** o antigo (mesmas chaves), então não acrescenta custo
  de escrita; cada entrada fica 2 bytes maior.

A troca foi feita como em produção (migration `historico_revisoes_indice_include_nota`):
`CREATE INDEX CONCURRENTLY` com outro nome, `DROP INDEX CONCURRENTLY` no antigo, `RENAME`. Um
`CREATE INDEX` comum bloqueia os `INSERT`s na tabela durante toda a construção, e
`CONCURRENTLY` não pode rodar dentro de uma transação (`autocommit_block()` no Alembic).

### Carga em massa: `ANALYZE` durante a carga

O seed com 200 alunos levava 64 s, e cada aluno era mais lento que o anterior (7 s nos
primeiros 50, 27 s nos últimos 50). Os `SELECT`s que leem de volta os ids de cada aluno usavam
`Seq Scan`: o planejador ainda achava, pelas estatísticas, que as tabelas estavam quase vazias.
Com `ANALYZE` a cada 25 alunos, dentro da própria transação, o tempo caiu para **17 s** e
ficou linear.

---

## 10. Testes com dados controlados

`tests/test_analytics.py` monta, em cada teste, um histórico pequeno cujo resultado é conhecido
de antemão:

| Teste | Dado montado | Resultado esperado |
|---|---|---|
| sequência | 7 dias seguidos, buraco, 5 dias até hoje | atual = 5, maior = 7 |
| sequência viva | estudou 10, 11, 12; consulta no 13 e no 14 | 3 no dia 13, 0 no dia 14 |
| dia local | 23h30 e 00h30 em São Paulo | 2 dias (em UTC seria 1) |
| semana vazia | atividade nas semanas 1 e 3 | semana 2 aparece com 0; LAG da 3 compara com a 2 |
| média móvel | 10/10, nada, 0/2 | 0,8333 (não 0,5) |
| GROUPING SETS | 2 revisões + 3 tentativas | linha "total" = 5 respostas, 3 acertos |
| N:N por material | card em 2 trechos de A e 1 de B | 1 resposta em A, 1 em B |
| ranking | erros 3, 2, 2, 1 | dense 1,2,2,3; rank 1,2,2,4; "top 2" traz 3 |
| previsão | atrasado, hoje, amanhã, dia 10 | 5 dias com 2,1,0,1,0 |
| calendário | 1, 2, 3, 4 revisões | níveis 1, 2, 3, 4; dias vazios nível 0 |
| custos | 2 meses, 2 tipos, 1 às 23h30 de 31/08 | acumulados exatos; 23h30 conta em agosto |
| materialized view | revisão depois do refresh | só aparece depois do próximo refresh |
