import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import Usuario
from app.servicos import auth

SENHA = "uma senha longa o bastante"


@pytest.fixture
def com_senha(session):
    u = Usuario(nome="Dona", email="dona@furg.br", senha_hash=auth.gerar_hash(SENHA))
    session.add(u)
    session.flush()
    return u


def login(client, email, senha):
    return client.post("/auth/login", data={"username": email, "password": senha})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------- login


def test_login_devolve_jwt_que_abre_as_rotas(client, com_senha):
    resposta = login(client, "DONA@furg.br", SENHA)  # e-mail sem diferenciar maiúsculas

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["token_type"] == "bearer"
    eu = client.get("/auth/eu", headers=bearer(corpo["access_token"]))
    assert eu.json()["email"] == "dona@furg.br"
    expira = datetime.fromisoformat(corpo["expira_em"])
    assert timedelta(days=29) < expira - datetime.now(UTC) <= timedelta(days=30)


def test_senha_errada_e_email_inexistente_dao_a_mesma_resposta(client, com_senha):
    errada = login(client, "dona@furg.br", "senha errada mas comprida")
    inexistente = login(client, "ninguem@furg.br", SENHA)

    assert errada.status_code == inexistente.status_code == 401
    assert errada.json() == inexistente.json()  # não revela quais e-mails existem


@pytest.mark.parametrize("curinga", ["%", "dona@%", "d_na@furg.br", "%@furg.br"])
def test_curingas_do_like_nao_casam_com_contas(client, com_senha, curinga):
    """Em LIKE/ILIKE, % e _ são curingas. Se o e-mail fosse comparado com ILIKE, "%"
    casaria com QUALQUER conta: com a senha certa entraria sem saber o e-mail, e cada
    variação ("%", "%%", "d%") seria uma chave nova no limite por e-mail."""
    assert login(client, curinga, SENHA).status_code == 401


def test_busca_do_email_usa_o_indice_de_lower(session, com_senha):
    """A comparação precisa ser lower(email) = ..., a MESMA expressão do índice único.
    Com 2 linhas na tabela o planejador preferiria o seq scan; desligá-lo mostra se o
    índice PODE ser usado (com ILIKE, não poderia nem assim)."""
    session.execute(text("SET LOCAL enable_seqscan = off"))
    plano = session.scalars(
        text("EXPLAIN (COSTS OFF) " + str(
            auth.consulta_por_email("dona@furg.br").compile(compile_kwargs={"literal_binds": True})
        ))
    ).all()
    assert any("uq_usuarios_email_lower" in linha for linha in plano), plano


def test_conta_sem_senha_nao_faz_login(client, usuario):
    assert usuario.senha_hash is None
    assert login(client, usuario.email, SENHA).status_code == 401


def test_senha_curta_e_recusada():
    with pytest.raises(ValueError, match="8 caracteres"):
        auth.gerar_hash("curta")


def test_cadastro_publico_nao_existe(client):
    resposta = client.post("/usuarios", json={"nome": "X", "email": "x@x.com"})
    assert resposta.status_code in (404, 405)


# ----------------------------------------------------------------- tokens


def test_token_expirado_e_recusado(client, com_senha):
    velho = auth.emitir_token(com_senha, agora=datetime.now(UTC) - timedelta(days=31))
    assert client.get("/auth/eu", headers=bearer(velho.valor)).status_code == 401


def test_token_adulterado_e_recusado(client, com_senha, outro_usuario):
    """Trocar o "sub" do conteúdo (base64, legível por qualquer um) invalida a assinatura."""
    cabecalho, conteudo, assinatura = auth.emitir_token(com_senha).valor.split(".")
    dados = json.loads(base64.urlsafe_b64decode(conteudo + "=="))
    dados["sub"] = str(outro_usuario.id)
    falsificado = base64.urlsafe_b64encode(json.dumps(dados).encode()).rstrip(b"=").decode()

    resposta = client.get("/auth/eu", headers=bearer(f"{cabecalho}.{falsificado}.{assinatura}"))

    assert resposta.status_code == 401


def test_token_sem_assinatura_alg_none_e_recusado(client, com_senha):
    sem_assinatura = jwt.encode(
        {"sub": str(com_senha.id), "ver": 0, "iat": datetime.now(UTC),
         "exp": datetime.now(UTC) + timedelta(days=1)},
        key=None, algorithm="none",
    )
    assert client.get("/auth/eu", headers=bearer(sem_assinatura)).status_code == 401


def test_token_assinado_com_outro_segredo_e_recusado(client, com_senha):
    alheio = jwt.encode(
        {"sub": str(com_senha.id), "ver": 0, "iat": datetime.now(UTC),
         "exp": datetime.now(UTC) + timedelta(days=1)},
        "x" * 64, algorithm="HS256",
    )
    assert client.get("/auth/eu", headers=bearer(alheio)).status_code == 401


def test_segredo_do_jwt_nao_aparece_nas_configuracoes():
    assert "**" in repr(get_settings().jwt_secret)


# --------------------------------------------------------------- revogação


def test_sair_de_todos_invalida_todos_os_tokens(client, com_senha):
    celular = auth.emitir_token(com_senha).valor
    notebook = auth.emitir_token(com_senha).valor

    assert client.post("/auth/sair-de-todos", headers=bearer(celular)).status_code == 204

    assert client.get("/auth/eu", headers=bearer(celular)).status_code == 401
    assert client.get("/auth/eu", headers=bearer(notebook)).status_code == 401
    novo = login(client, "dona@furg.br", SENHA).json()["access_token"]
    assert client.get("/auth/eu", headers=bearer(novo)).status_code == 200


# ------------------------------------------------------------ hash no banco


def test_hash_com_parametros_antigos_e_refeito_no_login(client, session):
    fraco = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    u = Usuario(nome="Antiga", email="antiga@furg.br", senha_hash=fraco.hash(SENHA))
    session.add(u)
    session.flush()

    assert login(client, "antiga@furg.br", SENHA).status_code == 200

    session.refresh(u)
    assert "m=65536,t=3,p=4" in u.senha_hash  # refeito com o custo atual


def test_banco_recusa_senha_em_texto_puro(session, usuario):
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.execute(
                text("UPDATE usuarios SET senha_hash = 'minha senha' WHERE id = :id"),
                {"id": usuario.id},
            )
    assert erro.value.orig.diag.constraint_name == "ck_usuarios_senha_hash_argon2id"


def test_mesma_senha_gera_hashes_diferentes():
    """O sal aleatório: dois usuários com a mesma senha não têm o mesmo hash."""
    assert auth.gerar_hash(SENHA) != auth.gerar_hash(SENHA)
