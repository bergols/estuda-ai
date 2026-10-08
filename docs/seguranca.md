# Segurança

Fase 6, parte 1. O que protege o estuda-ai e **por quê**: senhas, tokens, isolamento
entre usuários, SQL injection, proteção de custo, privilégios no banco, segredos e CI.
Cada seção aponta para o código e para os testes que provam a regra.

Mapa rápido:

| Ameaça | Defesa | Código | Testes |
|---|---|---|---|
| Vazamento do banco expõe senhas | argon2id com sal | `app/servicos/auth.py` | `test_auth.py` |
| Token falsificado ou adulterado | JWT HS256, algoritmo fixo | `auth.ler_token` | `test_auth.py` |
| Token roubado | expiração + "sair de todos" | `usuarios.versao_token` | `test_auth.py` |
| Força bruta no login | limite por IP e por e-mail | `routers/auth.py` | `test_limites.py` |
| Usuário A lê dados de B | filtro pelo dono em toda rota, 404 | `deps.py` | `test_isolamento.py` |
| SQL injection | bind parameters + allowlist de identificadores | todo `text()` | `test_sql_injection.py` |
| Conta gasta US$ 1000 de IA | rate limit + cota diária | `deps.protecao_ia` | `test_limites.py` |
| API comprometida apaga tabelas | papel com menor privilégio | migration `5d43d1f1af32` | `test_privilegios.py` |
| Chave commitada por engano | `.gitignore` + gitleaks no CI | `.gitleaks.toml` | job `segredos` |

---

## 1. Senhas: argon2id

O banco guarda `usuarios.senha_hash`, nunca a senha. Um hash de senha é diferente de
um hash comum (SHA-256): ele precisa ser **lento e caro de propósito**. Quem roubar o
banco vai testar senhas candidatas contra os hashes, e o SHA-256 roda bilhões de
vezes por segundo numa GPU.

**argon2id ou bcrypt?** Os dois são aceitáveis. Escolhi argon2id porque:

- é caro em **memória**, não só em CPU (aqui 64 MiB por tentativa). GPUs e chips
  dedicados têm milhares de núcleos e pouca memória por núcleo, então atacar em
  paralelo fica caro. O bcrypt usa só 4 KiB;
- venceu a Password Hashing Competition (2015) e é a primeira recomendação do OWASP;
- o bcrypt **ignora o que passa do 72º byte**: duas frases longas com o mesmo começo
  viram a mesma senha.

Parâmetros: os padrões do `argon2-cffi` (m=64 MiB, t=3, p=4, o perfil "low memory" da
RFC 9106), cerca de 40 ms por login. O hash carrega os próprios parâmetros e o sal:

```
$argon2id$v=19$m=65536,t=3,p=4$<sal aleatório>$<hash>
```

Daí três propriedades:

1. **Sal aleatório:** a mesma senha gera hashes diferentes a cada vez, então tabelas
   pré-calculadas (rainbow tables) não servem e dois usuários com a mesma senha não
   aparecem iguais.
2. **Custo atualizável:** se um dia o custo subir, `check_needs_rehash()` percebe no
   próximo login e refaz o hash. É o único momento em que temos a senha em mãos.
3. **CHECK no banco:** `ck_usuarios_senha_hash_argon2id` recusa qualquer valor que não
   comece com `$argon2id$`. Um bug que tentasse gravar a senha em texto puro é barrado
   pelo próprio Postgres.

Mais dois detalhes do login (`auth.autenticar`):

- **Mesma resposta** para "e-mail não existe" e "senha errada": senão o login vira um
  oráculo de quais e-mails têm conta.
- **Mesmo tempo:** quando o e-mail não existe, o código verifica a senha contra um hash
  falso. Sem isso, "e-mail inexistente" responderia em 1 ms e "senha errada" em 40 ms,
  e o **tempo** revelaria o que a mensagem esconde (timing attack).

Senha mínima de **8 caracteres**, o mínimo do NIST 800-63B para senha escolhida pela
pessoa. Começou em 12 e baixou a pedido do autor (12 incomodava no celular). O que
segura a força bruta pela internet não é o tamanho mínimo: é o limite de tentativas (5
falhas por e-mail e 20 por IP a cada 15 minutos), o custo do argon2id e a API só
alcançável pelo BFF. O tamanho pesa mais que a "complexidade" (letras, números,
símbolos), por isso não há regra de composição.

## 2. Tokens: JWT com expiração e revogação

Depois do login a API devolve um **JWT** (JSON Web Token): três partes em base64,
`cabeçalho.conteúdo.assinatura`.

```json
{"sub": "1", "ver": 0, "iat": 1791417600, "exp": 1794009600}
```

- O conteúdo **não é criptografado**: qualquer um decodifica o base64 e lê. Por isso
  ele não leva nada secreto (nem e-mail).
- Ele é **assinado** com HMAC-SHA256 e o `JWT_SECRET` do servidor. Mudar uma letra do
  conteúdo (trocar `"sub": "1"` por `"2"`) invalida a assinatura. Teste:
  `test_token_adulterado_e_recusado`.
- O servidor não guarda sessão: a cada requisição ele valida a assinatura e o `exp`.

**`algorithms=["HS256"]` é obrigatório** no `jwt.decode`. O cabeçalho do token diz qual
algoritmo foi usado, e o atacante controla o cabeçalho. Bibliotecas antigas aceitavam
`"alg": "none"` (token sem assinatura) quando o servidor não fixava a lista. Teste:
`test_token_sem_assinatura_alg_none_e_recusado`.

**Revogação.** O ponto fraco do JWT é que, sem sessão no servidor, não dá para
"desligar" um token roubado antes do `exp`. A solução daqui é um contador:
`usuarios.versao_token`. O token leva `"ver"`; a dependência `usuario_atual` confere se
ele ainda é igual ao do banco. `POST /auth/sair-de-todos` soma 1, e todos os tokens
emitidos antes passam a ser recusados. Custa uma leitura de `usuarios` por requisição,
que a rota já fazia para carregar o usuário.

**Por que 30 dias?** É um app pessoal, usado no celular; pedir senha toda semana seria
atrito sem ganho real. Os 30 dias só são aceitáveis porque existe o "sair de todos".
Configurável em `JWT_DIAS`.

**Força bruta no login.** No máximo `LIMITE_LOGIN_POR_IP` tentativas por IP e
`LIMITE_LOGIN_POR_EMAIL` **falhas** por e-mail a cada 15 minutos. O limite por e-mail
conta só as falhas (logar certo várias vezes não tranca a própria conta) e é conferido
**antes** da senha: depois de 5 erros, nem a senha certa entra até a janela virar.

**Cadastro público desabilitado.** Não existe rota de cadastro. A conta é criada pelo
script de admin:

```bash
docker compose exec backend python -m scripts.criar_usuario --email voce@exemplo.com --nome "Seu nome"
```

## 3. Isolamento: um usuário nunca vê dados de outro

Toda rota filtra pelo usuário do token. A peça central é a dependência
`DisciplinaDoUsuario`: ela busca a disciplina com `WHERE id = :id AND usuario_id =
:usuario`, e tudo o que fica abaixo da disciplina (materiais, cards, questões) é
acessado a partir dela.

**404, não 403.** Se a disciplina 42 é de outra pessoa, a resposta é "não encontrada".
Um 403 ("proibido") confirmaria que o id 42 existe.

`tests/test_isolamento.py` monta uma "vítima" com dados em todas as tabelas e um
"atacante" com token válido, e:

- chama **cada rota** com os ids da vítima e exige 404 (inclusive casos misturados:
  disciplina do atacante + material da vítima);
- chama as rotas sem id (listas, dashboard, gastos) e exige que nada da vítima apareça;
- confere que nada da vítima mudou;
- **meta-teste:** lê as rotas do OpenAPI e falha se alguma rota nova não tiver caso de
  isolamento. Assim ninguém esquece de pensar nisso ao criar uma rota.

Quando criei o teste, removi de propósito o filtro de dono de uma consulta para ver o
teste falhar (16 falhas). Um teste de segurança que nunca foi visto falhando pode estar
testando nada.

## 4. SQL injection

### O ataque

SQL injection acontece quando um **valor** vindo do usuário é colado no **texto** do SQL.
Exemplo do que **não** fazer:

```python
nome = request.json["nome"]
session.execute(text(f"SELECT * FROM disciplinas WHERE nome = '{nome}'"))
```

Com `nome = "x' OR '1'='1"`, o Postgres recebe:

```sql
SELECT * FROM disciplinas WHERE nome = 'x' OR '1'='1'
```

A aspa do usuário **fechou** a string, e o resto virou SQL: a condição é sempre
verdadeira e a consulta devolve as disciplinas de **todo mundo**. Com
`nome = "x'; DROP TABLE disciplinas; --"`, dependendo do driver, a tabela some.
"Escapar aspas na mão" não resolve: há codificações, barras invertidas e `$$` (o
dollar-quoting do Postgres) para esquecer.

### A defesa: bind parameters

```python
session.execute(text("SELECT * FROM disciplinas WHERE nome = :nome"), {"nome": nome})
```

O texto do SQL e os valores viajam **separados** até o Postgres (no protocolo, o
comando vai com `$1` e o valor vai num campo próprio). O Postgres faz o parse do SQL
**antes** de ver o valor; não há como o valor virar comando, qualquer que seja o seu
conteúdo. O ORM (`select(...).where(...)`) faz isso sozinho.

### A auditoria do SQL explícito

As fases 2 a 5 têm bastante SQL escrito à mão (`text()`), de propósito. Revisei cada
um. Todos os **valores** já iam como parâmetro. Havia um ponto que exige cuidado: os
**identificadores**. Nome de coluna e de tabela não podem ser bind parameter (o parse
precisa saber a coluna), então em `analytics.py` o nome da coluna de filtro era
interpolado:

```python
f"AND {coluna} = :disciplina_id"
```

Hoje `coluna` vem sempre de uma constante do código, nunca do usuário, mas basta um
refactor para isso mudar. A defesa é uma **allowlist** (`_coluna()`): só passa o que
casa com `[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?`. Aspas, espaços, `;` e `--` são
recusados com `ValueError` antes de chegar ao banco. Onde um identificador precisa vir
de fora, a ferramenta certa é `psycopg.sql.Identifier` (aspas duplas com escape).

Um caso parecido: `ALTER ROLE ... PASSWORD` (em `scripts/papel_app.py`) é um comando
utilitário e **não aceita** bind parameter. Ali a senha é composta com
`psycopg.sql.Literal`, que escapa corretamente; uma f-string seria uma injection
esperando uma senha com aspas.

`tests/test_sql_injection.py` manda payloads clássicos (`' OR '1'='1`, `'; DROP
TABLE`, `UNION SELECT senha_hash`, `$$`) no nome de disciplina, na busca (modos
textual, semântica e híbrida) e no login, e confere que o payload foi gravado ou
buscado como texto comum, que `' OR '1'='1` não vira "qualquer usuário" e que as
tabelas continuam lá. Datas e ids com payload nem chegam ao banco (a validação do
FastAPI devolve 422), e `_filtro`/`_periodo` recusam nomes de coluna fora da
allowlist.

### Injection sem SQL: os curingas do `LIKE`

Achei este durante a revisão para escrever este documento. O login buscava o e-mail com
`ILIKE`, para ignorar maiúsculas:

```python
select(Usuario).where(Usuario.email.ilike(email))   # ERRADO
```

O e-mail ia como bind parameter, então não havia SQL injection. Mas em `LIKE`/`ILIKE` o
**operador** interpreta o conteúdo do valor: `%` casa com qualquer sequência e `_` com
qualquer caractere. Resultado:

- `username = "%"` casava com **qualquer** conta. Com a senha certa, entrava sem saber o
  e-mail;
- cada variação (`%`, `%%`, `d%`, `%@furg.br`) virava uma chave diferente no limite de
  falhas por e-mail, que assim podia ser contornado;
- e `ILIKE` não usa o índice único de `lower(email)`.

A correção é comparar com a **mesma expressão do índice** (`auth.consulta_por_email`):

```python
select(Usuario).where(func.lower(Usuario.email) == email.strip().lower())
```

`test_curingas_do_like_nao_casam_com_contas` falhava antes da correção (as 4 variações
entravam) e passa depois; `test_busca_do_email_usa_o_indice_de_lower` confere no `EXPLAIN`
que o índice pode ser usado. Lição: bind parameter impede que o valor vire **comando**,
mas não muda o que o **operador** faz com o valor. Onde `LIKE` com entrada do usuário for
necessário (busca por prefixo, por exemplo), escape `%`, `_` e `\` antes.

### E se mesmo assim escapar?

Defesa em profundidade: a próxima seção garante que, mesmo com uma injection, o papel
da API **não consegue** `DROP TABLE`.

## 5. Proteção de custo: rate limit e cota diária

Cada geração com IA custa dinheiro. Se alguém conseguir um token (ou um bug fizer o
frontend repetir chamadas), a conta da Anthropic é o alvo. Duas camadas, na
dependência `ProtecaoIA` das rotas que chamam o LLM:

1. **Rate limit**, `LIMITE_IA_POR_MINUTO` por usuário: segura rajadas.
2. **Cota diária**, `LIMITE_GERACOES_DIA`: teto de gasto por dia, contado na própria
   tabela de auditoria `geracoes` (a fonte da verdade do gasto). "Hoje" começa à
   meia-noite no fuso do usuário.

A dependência vem **depois** de `DisciplinaDoUsuario`: pedir geração na disciplina de
outra pessoa continua dando 404 e não gasta a cota de ninguém. O 429 traz o cabeçalho
`Retry-After` com os segundos até a janela virar.

### Rate limit no Postgres (e não no Redis)

Contador de **janela fixa** numa tabela, com um UPSERT atômico:

```sql
INSERT INTO limites_taxa (chave, janela_inicio, contagem)
VALUES (:chave, date_bin(:janela, now(), timestamptz '2000-01-01 00:00+00'), 1)
ON CONFLICT (chave, janela_inicio)
DO UPDATE SET contagem = limites_taxa.contagem + 1
RETURNING contagem
```

- `date_bin` "arredonda para baixo" o instante até o começo da janela (com janela de
  15 min, 10:07 e 10:14 viram 10:00). Todas as requisições da mesma janela caem na
  mesma linha.
- `ON CONFLICT DO UPDATE` é **atômico**: duas requisições simultâneas não leem o mesmo
  valor e gravam o mesmo +1 (a "atualização perdida" da fase 4). A segunda espera o
  lock da linha e soma em cima. `test_limites.py` dispara 20 threads ao mesmo tempo e
  confere que a contagem final é exatamente 20.
- A tabela é `UNLOGGED`: não escreve no WAL (mais rápida) e é **zerada** se o
  Postgres cair. Para contadores de poucos minutos, perder tudo num crash é aceitável.
- O contador é commitado na hora, numa transação própria: se a requisição falhar
  depois, a tentativa continua contada (senão uma tentativa de login que dá erro não
  contaria).

Redis seria o padrão em escala, mas é mais um serviço para hospedar e pagar, e um
usuário só não chega perto do limite do Postgres. Janela fixa permite até 2x o limite
na virada da janela (fim de uma + começo da outra); para este uso tudo bem. Janela
deslizante ou token bucket corrigem isso com mais estado.

A cota diária é um limite "suave": entre contar e gravar a geração, duas requisições
simultâneas podem passar juntas (check-then-act). Passar 1 ou 2 do teto é aceitável; o
rate limit por minuto, que é atômico, segura rajadas.

## 6. Menor privilégio no banco

**Princípio:** cada parte do sistema recebe só as permissões de que precisa. Se a API for
comprometida (um bug, uma injection que escapou, uma dependência maliciosa), o estrago
fica limitado ao que o **papel** dela pode fazer.

Dois papéis no Postgres:

| Papel | Usado por | Pode |
|---|---|---|
| `estuda_ai` (dono) | migrations, scripts de admin, seeds | tudo no schema (DDL) |
| `estuda_ai_app` | a API e os testes | ler e escrever dados, tabela por tabela |

`DATABASE_URL` usa `estuda_ai_app`; `MIGRATION_DATABASE_URL` usa o dono. O compose roda
`alembic upgrade head` (como dono), depois `scripts/papel_app.py` (dá LOGIN e a senha de
`DATABASE_URL` ao papel; a migration não conhece segredos) e só então sobe a API.

O que o papel da API **não** pode, com teste em `tests/test_privilegios.py`:

- `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, criar índice ou função, desligar trigger;
- `UPDATE`/`DELETE` em `geracoes`, `historico_revisoes` e `tentativas`: histórico e
  auditoria de gasto são só-INSERT. Antes isso era garantido só pelo trigger; agora
  também por privilégio (e o trigger continua lá para o dono: defesa em profundidade);
- `DELETE` em `usuarios` (não há rota para apagar contas);
- ler `alembic_version`;
- dar `GRANT` a si mesmo.

Detalhes do Postgres que este desenho usa (conferidos num banco de laboratório antes
da migration):

- Inserir numa coluna `GENERATED ALWAYS AS IDENTITY` **não** exige privilégio na
  sequência.
- **As ações de FK rodam como o dono da tabela.** O app não tem `DELETE` em
  `historico_revisoes`, mas apagar uma disciplina ainda apaga o histórico em cascata.
- `REFRESH MATERIALIZED VIEW` exige ser **dono** da MV no Postgres 16 (o 17 criou o
  privilégio `MAINTAIN`). A saída é uma função `SECURITY DEFINER`, que roda com os
  privilégios de quem a criou:

```sql
CREATE FUNCTION atualizar_mv_respostas_diarias(...)
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
...
REVOKE EXECUTE ON FUNCTION atualizar_mv_respostas_diarias() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION atualizar_mv_respostas_diarias() TO estuda_ai_app;
```

`SECURITY DEFINER` é o `sudo` do Postgres, e tem duas armadilhas clássicas:

1. **`search_path`.** Sem fixá-lo, quem chama poderia criar uma tabela ou função com o
   mesmo nome num schema que vem antes e fazer o código privilegiado usá-la. O
   `pg_temp` vai **por último** de propósito: se não for listado, o Postgres procura
   tabelas nele **antes** de todos, e qualquer papel pode criar tabelas temporárias.
2. **`EXECUTE` para `PUBLIC`.** Por padrão **todo** papel pode executar uma função nova.
   O `REVOKE ... FROM PUBLIC` restringe ao app.

**Sem `DEFAULT PRIVILEGES`.** Seria cômodo dar ao app acesso automático a toda tabela
futura, mas aí uma tabela sensível nova nasceria aberta. Aqui cada tabela recebe o seu
`GRANT` na migration que a cria, e o teste da matriz (`test_matriz_de_privilegios_por_tabela`)
lê os privilégios **reais** do catálogo (`has_table_privilege`, que cobre MVs; o
`information_schema` não lista MVs) e compara com a tabela esperada. Tabela nova sem
decisão quebra o teste.

**Os testes rodam como `estuda_ai_app`.** Se um teste dependesse de um privilégio que a
API não tem, ele falharia aqui, e não só em produção. Os poucos testes de admin usam o
fixture `engine_dono`.

## 7. Segredos

- `.env` está no `.gitignore` e nunca foi commitado. O `.env.example` só tem valores de
  exemplo; chaves vão comentadas.
- `JWT_SECRET` tem no mínimo 32 caracteres (validado no `Settings`) e é um `SecretStr`
  (não aparece em `repr`, logs nem tracebacks). Gere com `openssl rand -hex 32`. Trocar o
  segredo invalida todos os tokens de todo mundo, o que é útil num incidente.
- `ANTHROPIC_API_KEY` é lida pelo SDK direto do ambiente; nunca passa pelo `Settings`.
- Em produção, as variáveis ficam no painel da plataforma de hospedagem, nunca no código.
- Senhas diferentes para o dono e para o app, e para cada ambiente.

### Varredura do histórico (gitleaks)

Um segredo apagado num commit novo **continua no histórico**: quem clona o repositório
recebe todos os commits. Por isso a varredura é no histórico inteiro (`fetch-depth: 0`
no CI), não só nos arquivos atuais. Se um dia aparecer uma chave real, apagar o arquivo
não basta: a chave tem de ser **revogada** no provedor (e só depois, se quiser, o
histórico reescrito).

A primeira varredura achou 2 ocorrências, ambas falsos positivos: linhas de planos do
`EXPLAIN` nos docs (um rótulo como "Group Key" seguido de dois-pontos e do nome de uma
coluna), que a regra genérica lê como "chave: valor". O `.gitleaks.toml` abre exceção só
para elas, com a linha inteira ancorada.

Outro falso positivo já commitado (esta própria seção citava o exemplo entre crases) está
no `.gitleaksignore`, pelo **fingerprint**: commit + arquivo + regra + linha. É a exceção
mais estreita possível; a mesma frase num commit novo seria detectada de novo. Uma tentativa anterior, com `paths` numa allowlist global, fazia o gitleaks
pular o **arquivo inteiro** (testado: uma chave falsa no mesmo doc passava). Exceções
largas escondem vazamentos de verdade.

## 8. No navegador: BFF, cookie httpOnly e CSRF

O frontend (fase 6, parte 2) guarda o JWT num cookie `httpOnly` e fala com a API por um
BFF no servidor do Next. Ali estão: por que não `localStorage` (um XSS levaria o token),
CSRF (`SameSite=Lax` + `Sec-Fetch-Site`), a allowlist do proxy (e o bug do `..`
encontrado pelos testes) e como a API recebe o IP real do cliente sem confiar num header
que qualquer um escreve (`X-Cliente-IP` só com `X-BFF-Segredo`). Ver
[`frontend.md`](frontend.md), seção 1.

## 9. CI

`.github/workflows/ci.yml`, a cada push e pull request:

- **backend:** Postgres 16 + pgvector como *service container* (o mesmo do compose),
  os dois papéis da produção, `ruff`, `alembic upgrade head`, `alembic check`,
  `downgrade base` + `upgrade head` e `pytest`. O `JWT_SECRET` é gerado no próprio job.
  Sem o grupo de dependências `modelo` (torch, ~1 GB): os testes usam o `EmbedderFalso`.
- **segredos:** gitleaks em versão fixa no histórico inteiro.
- O token do GitHub no workflow só tem `contents: read`: menor privilégio também no CI.
