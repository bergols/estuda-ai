def test_health_informa_banco_e_pgvector(client):
    resposta = client.get("/health")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["banco"] == "ok"
    assert corpo["pgvector"]  # a migration habilitou a extensão
