from tests.conftest import cabecalho


def criar(client, headers, nome, descricao=None):
    return client.post("/disciplinas", json={"nome": nome, "descricao": descricao}, headers=headers)


# --------------------------------------------------------------------- criar


def test_criar_disciplina(client, headers):
    resposta = criar(client, headers, "  Banco de Dados  ", "AED II")

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["id"] > 0
    assert corpo["nome"] == "Banco de Dados"  # espaços removidos pelo schema
    assert corpo["descricao"] == "AED II"
    assert corpo["criado_em"] and corpo["atualizado_em"]


def test_nome_duplicado_ignorando_maiusculas_retorna_409(client, headers):
    criar(client, headers, "Cálculo I")

    resposta = criar(client, headers, "CÁLCULO i")

    assert resposta.status_code == 409
    # Depois do erro a sessão continua utilizável (o rollback foi feito)
    assert criar(client, headers, "Cálculo II").status_code == 201


def test_mesmo_nome_em_usuarios_diferentes_e_permitido(client, headers, outro_usuario):
    criar(client, headers, "Física")

    resposta = criar(client, cabecalho(outro_usuario), "Física")

    assert resposta.status_code == 201


def test_nome_vazio_retorna_422(client, headers):
    assert criar(client, headers, "   ").status_code == 422


# -------------------------------------------------------------------- listar


def test_listar_mostra_so_as_do_usuario_em_ordem_alfabetica(client, headers, outro_usuario):
    for nome in ["química", "Álgebra", "Banco de Dados"]:
        criar(client, headers, nome)
    criar(client, cabecalho(outro_usuario), "De outra pessoa")

    resposta = client.get("/disciplinas", headers=headers)

    assert resposta.status_code == 200
    nomes = [d["nome"] for d in resposta.json()]
    # A ordem vem da collation do banco (en_US.utf8): "Á" fica junto do "A".
    # Com a collation "C" (ordem de bytes), "Álgebra" iria para o fim.
    assert nomes == ["Álgebra", "Banco de Dados", "química"]
    assert "De outra pessoa" not in nomes


def test_listar_com_paginacao(client, headers):
    for nome in ["A", "B", "C", "D"]:
        criar(client, headers, nome)

    pagina = client.get("/disciplinas", params={"limit": 2, "offset": 1}, headers=headers)

    assert [d["nome"] for d in pagina.json()] == ["B", "C"]


def test_limit_fora_do_intervalo_retorna_422(client, headers):
    assert client.get("/disciplinas", params={"limit": 0}, headers=headers).status_code == 422


# --------------------------------------------------------------------- obter


def test_obter_disciplina(client, headers):
    criada = criar(client, headers, "Grafos").json()

    resposta = client.get(f"/disciplinas/{criada['id']}", headers=headers)

    assert resposta.status_code == 200
    assert resposta.json() == criada


def test_disciplina_de_outro_usuario_retorna_404(client, headers, outro_usuario):
    alheia = criar(client, cabecalho(outro_usuario), "Privada").json()

    assert client.get(f"/disciplinas/{alheia['id']}", headers=headers).status_code == 404
    assert client.patch(
        f"/disciplinas/{alheia['id']}", json={"nome": "x"}, headers=headers
    ).status_code == 404
    assert client.delete(f"/disciplinas/{alheia['id']}", headers=headers).status_code == 404


def test_disciplina_inexistente_retorna_404(client, headers):
    assert client.get("/disciplinas/999999", headers=headers).status_code == 404


# ----------------------------------------------------------------- atualizar


def test_atualizar_altera_so_os_campos_enviados(client, headers):
    criada = criar(client, headers, "Redes", "camadas").json()

    resposta = client.patch(
        f"/disciplinas/{criada['id']}", json={"descricao": "TCP/IP"}, headers=headers
    )

    assert resposta.status_code == 200
    assert resposta.json()["nome"] == "Redes"
    assert resposta.json()["descricao"] == "TCP/IP"


def test_atualizar_descricao_para_nulo_e_permitido(client, headers):
    criada = criar(client, headers, "Redes", "camadas").json()

    resposta = client.patch(
        f"/disciplinas/{criada['id']}", json={"descricao": None}, headers=headers
    )

    assert resposta.json()["descricao"] is None


def test_atualizar_nome_para_nulo_retorna_422(client, headers):
    criada = criar(client, headers, "Redes").json()

    resposta = client.patch(f"/disciplinas/{criada['id']}", json={"nome": None}, headers=headers)

    assert resposta.status_code == 422


def test_atualizar_para_nome_ja_usado_retorna_409(client, headers):
    criar(client, headers, "Compiladores")
    outra = criar(client, headers, "Autômatos").json()

    resposta = client.patch(
        f"/disciplinas/{outra['id']}", json={"nome": "compiladores"}, headers=headers
    )

    assert resposta.status_code == 409


# -------------------------------------------------------------------- apagar


def test_apagar_disciplina(client, headers):
    criada = criar(client, headers, "Temporária").json()

    resposta = client.delete(f"/disciplinas/{criada['id']}", headers=headers)

    assert resposta.status_code == 204
    assert client.get(f"/disciplinas/{criada['id']}", headers=headers).status_code == 404


# ---------------------------------------------------------- identificação


def test_sem_token_retorna_401(client):
    resposta = client.get("/disciplinas")
    assert resposta.status_code == 401
    assert resposta.headers["www-authenticate"] == "Bearer"


def test_token_invalido_retorna_401(client):
    assert client.get("/disciplinas", headers={"Authorization": "Bearer abc"}).status_code == 401
