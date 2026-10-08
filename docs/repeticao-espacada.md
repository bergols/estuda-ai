# Repetição espaçada (SM-2) — estuda-ai

A Fase 4 agenda a revisão dos flashcards com o algoritmo SM-2. As decisões de banco desta
fase: separar **estado atual** de **histórico**, escolher o índice da fila do dia (provado
com `EXPLAIN ANALYZE`), proteger a revisão contra **requisições concorrentes**, calcular
"hoje" no **fuso do usuário** e comparar a lógica **no banco** (PL/pgSQL) com a lógica **na
aplicação** (Python).

| Rota | O que faz |
|---|---|
| `GET /revisoes/hoje?disciplina_id=&limite=20` | cards vencidos até o fim de hoje (no fuso do usuário), do mais atrasado ao menos |
| `POST /revisoes/{flashcard_id}` `{nota, versao}` | aplica o SM-2; 409 se a versão estiver desatualizada |

---

## 1. O algoritmo SM-2

Depois de ver a resposta, o aluno dá uma nota:

| Nota | Significado | Efeito na facilidade (EF) |
|---:|---|---:|
| 5 | lembrou perfeitamente | +0,10 |
| 4 | lembrou após hesitar | 0 |
| 3 | lembrou com dificuldade | −0,14 |
| 2 | errou, mas a resposta pareceu fácil | −0,32 |
| 1 | errou; lembrou ao ver a resposta | −0,54 |
| 0 | branco total | −0,80 |

- Nota ≥ 3: o intervalo vai para 1 dia, depois 6, depois `intervalo anterior × EF`; as
  repetições sobem 1.
- Nota < 3: recomeça (repetições 0, intervalo 1 dia).
- `EF' = EF + (0,1 − (5 − nota) × (0,08 + (5 − nota) × 0,02))`, nunca abaixo de 1,3.

Um card respondido sempre com nota 4 (EF fica em 2,5) é revisto depois de 1, 6, 15, 38 e
95 dias. Um card difícil perde EF e passa a voltar com mais frequência. A implementação
usada pela API está em `app/servicos/sm2.py`.

---

## 2. Estado atual × histórico

São duas perguntas diferentes, e cada uma tem sua tabela:

| | `revisoes` (estado) | `historico_revisoes` (histórico) |
|---|---|---|
| Responde | "quando revejo este card e com que EF?" | "o que aconteceu em cada revisão?" |
| Linhas | **uma por card** (1:1) | **uma por revisão feita** |
| Operações | `UPDATE` a cada revisão | **só `INSERT`** (um trigger recusa `UPDATE`) |
| Colunas | facilidade, intervalo, repetições, próxima revisão, versão | nota, estado **antes** e **depois**, quando foi feita |
| Quem usa | fila do dia (consulta mais frequente) | dashboard da fase 5, auditoria, recalcular |

### Por que guardar o histórico

O estado atual é só o **último** resultado; ele esquece como chegou ali. Sem histórico não
dá para responder perguntas como:
- qual a taxa de acerto por semana ou por tópico? (fase 5)
- o aluno está ficando mais atrasado? (`revisado_em − proxima_revisao_anterior`)
- quanto tempo um card leva para "amadurecer"?
- se o SM-2 mudar (outra fórmula, outro piso de EF), como recalcular o estado de todos os
  cards? (é só reaplicar o histórico com as notas, em ordem)

Essa última pergunta mostra a relação entre as duas tabelas: **o estado é derivável do
histórico**. Ele é uma desnormalização (uma cópia pré-calculada), guardada porque a fila do
dia precisa dele em microssegundos, e reprocessar o histórico a cada consulta seria caro. É
a mesma ideia de *event sourcing*: os eventos (histórico) são a verdade, e o estado é uma
"projeção" mantida para leitura rápida.

O histórico guarda o estado **antes e depois** (`facilidade_anterior`/`facilidade_nova`,
`intervalo_anterior`/`intervalo_novo`...). O "antes" de uma linha é o "depois" da anterior, e
daria para obtê-lo com `LAG()`. Mesmo assim fica gravado, para cada linha ser legível
sozinha e para o "antes" ficar registrado exatamente como o SM-2 o viu.

### Imutável de verdade

```sql
CREATE TRIGGER trg_historico_revisoes_imutavel BEFORE UPDATE ON historico_revisoes
FOR EACH ROW EXECUTE FUNCTION impedir_alteracao_historico();  -- RAISE EXCEPTION
```

`DELETE` continua permitido, para a cascata quando um card é apagado. Um `REVOKE UPDATE` não
resolveria aqui, porque a aplicação conecta com o dono das tabelas, e o dono ignora
permissões nas próprias tabelas. O trigger vale para qualquer usuário.

### Por que o estado saiu de `flashcards`

Até a Fase 3, o estado do SM-2 eram colunas de `flashcards`. Mudou por causa de como o
Postgres faz `UPDATE`:

- **MVCC**: um `UPDATE` não altera a linha no lugar. Ele grava uma **versão nova da linha
  inteira**, e a antiga vira uma "tupla morta" que o VACUUM limpa depois. É assim que
  leitores antigos continuam vendo a versão antiga sem precisar de lock.
- Uma linha de `flashcards` tem frente, verso e um embedding de 1.544 bytes. Cada revisão
  reescreveria ~2 KB e deixaria uma tupla morta desse tamanho.
- Em `revisoes`, com ~60 bytes por linha, cada revisão reescreve só o estado.

Conteúdo (muda quase nunca) e agendamento (muda a cada revisão) têm ritmos diferentes.
Separá-los em duas tabelas 1:1 é uma **partição vertical**. Bônus: se um dia vários alunos
estudarem o mesmo card, o estado vira `(usuario_id, flashcard_id)` sem mexer no conteúdo.

### Como se modela 1:1

```sql
CREATE TABLE revisoes (
    flashcard_id  bigint PRIMARY KEY,        -- a PK É a FK: impossível ter 2 estados por card
    disciplina_id bigint NOT NULL,
    ...
    FOREIGN KEY (flashcard_id, disciplina_id) REFERENCES flashcards (id, disciplina_id)
        ON DELETE CASCADE ON UPDATE CASCADE
);
```

- `disciplina_id` é uma cópia (para a fila filtrar sem JOIN), protegida pela FK composta:
  o mesmo padrão de `trechos.disciplina_id` na Fase 2.
- "Todo card tem estado" é garantido por um trigger `AFTER INSERT ON flashcards`, que cria a
  linha. Inclusive em `COPY`, que também dispara triggers de linha (o seed usa isso).
- `CHECK ((versao = 0) = (ultima_revisao_em IS NULL))`: versão 0 significa nunca revisado.

### A migração (preservando os dados)

A migration `estado_sm2_em_revisoes` renomeou a tabela antiga para `historico_revisoes`.
Renomear a tabela **não** renomeia a sequência da coluna identity, as constraints nem os
índices; cada um foi renomeado à mão para seguir a convenção de nomes. As colunas
"anteriores" das revisões já existentes foram preenchidas com uma window function:

```sql
coalesce(lag(facilidade_nova) OVER (PARTITION BY flashcard_id ORDER BY revisado_em), 2.50)
```

`LAG(x)` é o valor de `x` na linha anterior da mesma partição; na primeira revisão de cada
card não há anterior, e o `coalesce` usa o estado inicial.

---

## 3. A fila do dia e o índice certo

```sql
-- fila de uma disciplina
SELECT ... FROM revisoes r JOIN disciplinas d ... JOIN flashcards f ...
WHERE r.disciplina_id = :disciplina_id AND d.usuario_id = :usuario_id
  AND r.proxima_revisao < :fim_de_hoje
ORDER BY r.proxima_revisao, r.flashcard_id
LIMIT 20;
```

O índice `(disciplina_id, proxima_revisao)` atende exatamente essa forma: pula para a
disciplina, já percorre os vencidos **em ordem de atraso**, e o `LIMIT` para depois de 20
entradas. Sem ordenar nada.

### O experimento ([experimentos/fila-do-dia.md](experimentos/fila-do-dia.md))

200 mil cards criados e desfeitos numa transação, em dois cenários com o **mesmo total**:
A com 50 usuários (4 mil cards cada) e B com 500 usuários (400 cada). Mediana do
`Execution Time`, em ms:

| Índice | A: usuário (JOIN) | A: usuário (LATERAL) | A: 1 disciplina | B: usuário (LATERAL) | B: 1 disciplina |
|---|---:|---:|---:|---:|---:|
| Sem índice | 41,8 | 58,2 | 37,8 | 75,2 | 78,2 |
| `(proxima_revisao)` | **0,90** | 3,71 | 0,96 | 60,9 | **77,9** |
| `(disciplina_id)` | 3,00 | 1,02 | 0,74 | 0,26 | 0,08 |
| **`(disciplina_id, proxima_revisao)`** | 2,42 | **0,24** | **0,07** | **0,20** | **0,06** |
| `(proxima_revisao, disciplina_id)` | 0,86 | 0,46 | 0,15 | 2,13 | 0,52 |

Três lições, em ordem de descoberta:

1. **O composto ganhou na fila de uma disciplina (0,07 ms), mas perdeu na do usuário
   (2,42 ms contra 0,90 ms do índice simples).** Ele ordena primeiro por disciplina, então
   não entrega em ordem de data as linhas de **várias** disciplinas juntas. O planejador
   lia todos os vencidos do usuário e depois ordenava.
2. **A correção foi reescrever a consulta, não o índice**: um `CROSS JOIN LATERAL` por
   disciplina pega os 20 mais atrasados de **cada** disciplina pelo índice (o padrão
   "top-N por grupo") e ordena só essas linhas, no máximo 4 × 20. A fila do usuário caiu
   para 0,24 ms:
   ```sql
   FROM disciplinas d
   CROSS JOIN LATERAL (
       SELECT * FROM revisoes r2
       WHERE r2.disciplina_id = d.id AND r2.proxima_revisao < :fim_de_hoje
       ORDER BY r2.proxima_revisao LIMIT 20
   ) r
   WHERE d.usuario_id = :usuario_id
   ORDER BY r.proxima_revisao LIMIT 20
   ```
3. **O índice simples em `proxima_revisao` só ia bem por ter poucos usuários.** Ele
   percorre a fila de vencimentos **do sistema inteiro** até achar 20 cards do usuário.
   Com o mesmo total dividido entre 500 usuários, a fila de uma disciplina foi para 78 ms.
   O composto depende só dos dados do próprio usuário: 0,06 ms nos dois cenários. Ao
   escolher índices, pense em como a consulta **escala**, não só no tempo de hoje.

Duas decisões extras na forma da consulta:

- **Duas consultas, e não `WHERE (:disciplina_id IS NULL OR r.disciplina_id = :disciplina_id)`.**
  Esse "OR com parâmetro opcional" é um antipadrão: num plano genérico (reaproveitado
  entre execuções) o planejador não sabe qual lado do OR vale e tende a não usar o índice.
  Aqui, além disso, as duas filas pedem **formas** diferentes de consulta.
- `disciplina_id` de outro usuário responde 404 antes da consulta.

### Por que não um índice parcial

O índice "perfeito" conteria só os cards vencidos:

```sql
CREATE INDEX ... ON revisoes (disciplina_id, proxima_revisao) WHERE proxima_revisao <= now();
-- ERROR: functions in index predicate must be marked IMMUTABLE
```

O predicado de um índice parcial é avaliado **quando a linha é gravada**, e precisa dar
sempre o mesmo resultado. `now()` muda a cada instante: um card gravado com vencimento para
amanhã venceria amanhã sem nunca entrar no índice. Índice parcial serve para condições
**estáveis da própria linha** (como o `WHERE correta` da Fase 3, ou um futuro
`WHERE NOT suspenso` para cards que o aluno pausou). Para uma janela de tempo que anda, o
índice composto com a data como 2ª coluna faz o papel do "parcial": o `<` vira um intervalo
contíguo dentro do índice.

---

## 4. Registrar a revisão: uma transação

```text
1. SELECT estado (com JOIN para conferir que o card é do usuário)
2. calcular SM-2 (Python)
3. UPDATE revisoes ... WHERE flashcard_id = :id AND versao = :versao RETURNING versao
4. INSERT INTO historico_revisoes (...)
5. COMMIT
```

Os passos 3 e 4 são atômicos: ou o estado muda **e** o histórico ganha a linha, ou nenhum
dos dois. O teste `test_falha_no_historico_desfaz_o_update_do_estado` força uma falha
depois do `UPDATE`: o `ROLLBACK` desfaz o estado (a versão continua 0) e o histórico fica
vazio.

---

## 5. Concorrência: o duplo clique

Duas requisições com a mesma revisão chegam juntas (duplo clique, duas abas). As duas leem
o mesmo estado antes de qualquer uma gravar.

**Sem controle** (`test_sem_controle_o_card_e_processado_em_dobro_e_perde_atualizacao`):
as duas leem `repeticoes = 0`, as duas calculam `repeticoes = 1` e as duas gravam. Resultado:
duas linhas no histórico (processado em dobro) e `repeticoes = 1` em vez de 2, porque a
segunda escrita apagou a primeira. Esse é o **lost update**, a anomalia clássica de
"ler, calcular, escrever".

### As duas abordagens

| | Pessimista: `SELECT ... FOR UPDATE` | Otimista: coluna `versao` |
|---|---|---|
| Ideia | trava a linha ao ler; os outros **esperam** | não trava; ao gravar, **confere** se ninguém mudou |
| SQL | `SELECT ... FOR UPDATE` → calcula → `UPDATE` | `UPDATE ... WHERE versao = :v` (e `versao = versao + 1`) |
| Lost update | evita | evita |
| Duplo clique | a 2ª espera, lê o estado **novo** e revisa **de novo** | a 2ª atualiza 0 linhas → **409** |
| Lock segurado | durante toda a transação (incluindo o cálculo) | só durante o próprio `UPDATE` |
| Precisa do cliente | nada | o cliente manda a `versao` que viu |
| Bom quando | muita contenção; a 2ª requisição **deve** ser aplicada depois da 1ª (ex.: debitar saldo) | pouca contenção; a 2ª requisição é **repetida** ou está desatualizada |

O teste `test_for_update_serializa_mas_ainda_processa_em_dobro` mostra o ponto principal: com
`FOR UPDATE` não há lost update (`repeticoes` chega a 2), mas o card é revisado **duas
vezes**. O lock ordena as requisições, mas não sabe que a segunda é uma repetição da
primeira.

### A escolha: controle otimista

O problema aqui não é ordenar escritas concorrentes legítimas; é **detectar uma requisição
repetida ou desatualizada**. A `versao` faz exatamente isso: a fila devolve a versão de cada
card, o cliente a manda de volta, e uma segunda requisição com a mesma versão encontra o card
já mudado.

Como o `UPDATE ... WHERE versao = :v` funciona sob concorrência (nível READ COMMITTED,
o padrão do Postgres):
1. A requisição 1 faz o `UPDATE` e trava a linha (lock de linha, até o COMMIT).
2. A requisição 2 tenta o mesmo `UPDATE` e **espera** o lock.
3. A 1 dá COMMIT. A 2 acorda, **reavalia o `WHERE` contra a versão nova da linha**
   (`versao` agora é 1), a condição `versao = 0` falha e ela atualiza **0 linhas**.
4. `RETURNING` vazio → a API responde 409 com a `versao_atual`.

É o mesmo *compare-and-set* que a Fase 2 usou para reservar materiais (`WHERE status =
'pendente'`). O teste `test_controle_otimista_processa_uma_vez_e_a_outra_recebe_409` roda as
duas requisições em threads com conexões reais e uma `Barrier` que garante que as duas leram
antes de qualquer escrita: uma recebe 200, a outra 409, e fica uma única linha no histórico.

---

## 6. Fuso horário

### `timestamptz` em tudo

`timestamptz` guarda um **instante** (internamente em UTC) e o exibe no fuso da sessão.
`timestamp` sem fuso guarda um "horário de parede" sem dizer de onde. `proxima_revisao`,
`ultima_revisao_em` e `revisado_em` são todos `timestamptz`.

### "Hoje" é do usuário

Um card está na fila de hoje se vence **antes da próxima meia-noite no fuso do usuário**
(`usuarios.fuso_horario`, padrão `America/Sao_Paulo`):

```sql
(date_trunc('day', :agora AT TIME ZONE u.fuso_horario) + interval '1 day')
    AT TIME ZONE u.fuso_horario
```

Lendo de dentro para fora: `timestamptz AT TIME ZONE fuso` dá o **horário de parede local**
(um `timestamp`); `date_trunc('day', ...)` vai para a meia-noite local de hoje; `+ 1 day` vai
para a de amanhã; e `timestamp AT TIME ZONE fuso` converte de volta para um instante. O mesmo
operador faz as duas conversões, e o que decide a direção é o tipo da entrada.

### O que dá errado se fizer errado

- **"Hoje" em UTC** (`now()::date` numa sessão em UTC, ou `datetime.utcnow().date()`): às
  22h30 de 07/10 em São Paulo já é 01h30 de 08/10 em UTC. A fila "de hoje" mostraria os
  cards de amanhã na noite anterior, e o aluno revisaria cedo demais. Teste:
  `test_hoje_e_o_dia_do_usuario_e_nao_o_dia_em_utc`.
- **"Hoje" no fuso do servidor**: funciona por acaso enquanto servidor e aluno estão no
  mesmo fuso, e quebra quando o servidor vai para a nuvem (quase sempre em UTC) ou quando
  aparece um aluno em outro fuso. O mesmo instante é 08/10 em Tóquio e 07/10 em São Paulo
  (`test_o_mesmo_instante_e_outro_dia_em_outro_fuso`).
- **Guardar `timestamp` sem fuso**: `2026-10-07 22:30` é um horário de São Paulo ou de
  Lisboa? Não há como saber depois.
- **Horário de verão**: `timestamptz + interval '1 day'` soma um dia **de calendário** no
  fuso da sessão (23 ou 25 h na troca de horário), enquanto `+ interval '24 hours'` soma 24 h
  exatas. O Brasil não tem horário de verão desde 2019, mas `America/New_York` tem (exercício
  4.3). Por isso a próxima revisão é calculada em Python, como instante UTC
  (`revisado_em + timedelta(days=n)`, n × 24 h), sem depender do fuso da sessão do banco.
- **Atualizações do tzdata**: regras de fuso mudam por lei (o fim do horário de verão
  brasileiro foi uma delas). O Postgres traz sua base de fusos, que precisa estar atualizada.
  Por isso `fuso_horario` é validado por um **trigger** (que tenta usar o fuso) e não por um
  `CHECK`: um CHECK deve depender só da própria linha e dar sempre o mesmo resultado, e não
  pode consultar `pg_timezone_names`.

---

## 7. SM-2 em PL/pgSQL × em Python

A mesma conta existe nos dois lugares: `sm2()` no banco (migration `funcao_sm2_plpgsql`) e
`calcular()` em `app/servicos/sm2.py`. O teste `test_python_e_plpgsql_concordam_em_todo_o_grid`
compara 56 estados × 6 notas = **336 casos** numa única consulta
(`VALUES ... CROSS JOIN LATERAL sm2(...)`), e todos batem.

**Achado do experimento:** a primeira tentação em Python seria
`round(intervalo * float(ef))`. O `round()` do Python arredonda o meio **para o par**
(`round(12.5) == 12`, "arredondamento bancário"), e o `round(numeric)` do Postgres arredonda o
meio **para longe do zero** (13). Com `float` e `round()`, **21 dos 336 casos** dariam um
intervalo diferente do banco (ex.: 15 × 2,7 = 40,5 → 40 em Python, 41 no banco). A versão
Python usa `Decimal` com `ROUND_HALF_UP`. Duas implementações da mesma regra só ficam
iguais com um teste que compare as duas.

`sm2()` é declarada `IMMUTABLE STRICT`:
- `IMMUTABLE`: mesma entrada, mesma saída, sem ler tabelas. O planejador pode avaliá-la
  uma vez quando os argumentos são constantes, e ela pode ser usada em índices de expressão
  e colunas geradas. (Declarar `IMMUTABLE` uma função que não é traz resultados errados, e o
  Postgres **não confere**.)
- `STRICT`: se algum argumento for `NULL`, devolve `NULL` sem executar.

| | Lógica no banco (PL/pgSQL) | Lógica na aplicação (Python) |
|---|---|---|
| Idas ao banco | a revisão inteira pode ser **um** `UPDATE ... FROM sm2(...)` | ler, calcular, gravar |
| Vale para qualquer cliente | sim (psql, scripts, outra linguagem) | só para quem usar o código Python |
| Testes | precisam de um banco | testes de unidade puros, instantâneos |
| Depuração e ferramentas | `RAISE NOTICE`; pouca ferramenta | debugger, linters, tipos |
| Versionamento e deploy | via migration (trocar a função = nova migration) | junto com o código |
| Escalabilidade | CPU do banco, o recurso mais difícil de escalar | CPU da aplicação, fácil de multiplicar |
| Portabilidade | presa ao Postgres | independente do banco |

**A API usa a versão em Python.** O SM-2 é regra de negócio que pode evoluir (outras
fórmulas, ajustes por aluno), é testado sem banco em milissegundos, e o custo das idas ao
banco é pequeno (a leitura e a escrita já existiriam por causa do histórico). Lógica no banco
compensa quando a regra é **integridade** (o que nunca pode ser violado, venha de onde vier:
constraints, os triggers de imutabilidade e de alternativas) ou quando processar **perto dos
dados** evita mover muitas linhas (agregações, cargas em massa). O SM-2 não é nenhum dos
dois. A função `sm2()` fica no banco como experimento e como ferramenta de análise: dá para
simular "e se" diretamente em SQL.

---

## 8. Dados simulados para a fase 5

`scripts/seed_revisoes.py` cria `estudante@estuda-ai.local` e simula 42 dias de estudo no
fuso de São Paulo: faltas (mais no fim de semana, gerando atraso), cards novos entrando aos
poucos, de 30 a 100 revisões por dia com notas sorteadas pela dificuldade do tópico, pelo
atraso e pela maturidade do card, e questões respondidas com um distrator "mais tentador".
Gera 480 cards, cerca de 2 mil revisões, 60 questões e 140 tentativas. Na fase 5 ganhou
apostilas (N:N), gerações de IA simuladas e `--alunos N` para volume; ver `analytics.md`.

Carga em massa, numa transação:
- `COPY` dos cards (os triggers de linha disparam também em `COPY`, então o estado de cada
  card é criado);
- `COPY` do histórico;
- o estado final de todos os cards num **único** `UPDATE revisoes ... FROM estado_final`, a
  partir de uma tabela temporária (`CREATE TEMP TABLE ... ON COMMIT DROP`) preenchida por
  `COPY`, em vez de 480 `UPDATE`s.
