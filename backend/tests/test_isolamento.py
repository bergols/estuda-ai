"""Um usuário NUNCA acessa dados de outro.

A "vítima" tem disciplina, material, card (com histórico), questão (com tentativa)
e gerações. O "atacante" (o `usuario` dos fixtures, com token válido) tenta cada
rota da API com os ids da vítima. Toda rota que recebe um id da vítima tem de
responder 404 (e não 403: 403 confirmaria que o id existe), e toda rota sem id não
pode mostrar nada da vítima.

O meta-teste no fim lê as rotas do OpenAPI: uma rota nova sem caso aqui faz o teste
falhar, para ninguém esquecer de pensar no isolamento dela.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.main import app
from app.models import (
    Alternativa,
    Disciplina,
    Flashcard,
    Geracao,
    HistoricoRevisao,
    Material,
    Questao,
    Revisao,
    SessaoEstudo,
    Tentativa,
    Trecho,
)
from app.servicos import analytics
from tests.conftest import cabecalho, criar_pdf

PUBLICAS = {("GET", "/health"), ("POST", "/auth/login")}
CHAVE_DA_VITIMA = uuid.uuid4()


@pytest.fixture
def vitima(session, outro_usuario, usuario):
    agora = datetime.now(UTC)
    d = Disciplina(usuario_id=outro_usuario.id, nome="Segredos da vítima")
    session.add(d)
    session.flush()
    m = Material(disciplina_id=d.id, titulo="Apostila secreta", tipo="texto", status="erro",
                 erro_mensagem="x", hash_sha256="9" * 64, tamanho_bytes=1)
    session.add(m)
    session.flush()
    t = Trecho(material_id=m.id, disciplina_id=d.id, ordem=0, conteudo="conteúdo secreto")
    c = Flashcard(disciplina_id=d.id, frente="pergunta secreta", verso="resposta")
    q = Questao(disciplina_id=d.id, enunciado="questão secreta", tipo="multipla_escolha",
                alternativas=[Alternativa(letra="A", texto="a", correta=True),
                              Alternativa(letra="B", texto="b")])
    session.add_all([t, c, q])
    session.flush()
    session.add(HistoricoRevisao(
        flashcard_id=c.id, nota=4, facilidade_anterior=Decimal("2.5"), facilidade_nova=Decimal("2.5"),
        intervalo_anterior=0, intervalo_novo=1, repeticoes_anterior=0, repeticoes_nova=1,
        proxima_revisao_anterior=agora - timedelta(days=1), proxima_revisao_nova=agora,
        revisado_em=agora - timedelta(hours=1)))
    session.add(Tentativa(questao_id=q.id, alternativa_id=q.alternativas[0].id, correta=True,
                          respondida_em=agora - timedelta(hours=1)))
    session.add(Geracao(usuario_id=outro_usuario.id, disciplina_id=d.id, tipo="pergunta",
                        modelo="claude-haiku-4-5-20251001", custo_usd=Decimal("0.5"),
                        duracao_ms=1, status="sucesso"))
    # Sessão de estudo da vítima (fase 7), com chave conhecida
    session.add(SessaoEstudo(usuario_id=outro_usuario.id, disciplina_id=d.id, chave=CHAVE_DA_VITIMA,
                             metodo="pomodoro", foco_min=25, pausa_min=5, ciclos=1,
                             meta="meta secreta", sistema="macos", status="em_andamento",
                             iniciada_em=agora - timedelta(hours=1)))
    # disciplina do PRÓPRIO atacante, para os casos "misturados"
    propria = Disciplina(usuario_id=usuario.id, nome="Minha")
    session.add(propria)
    session.flush()
    analytics.atualizar_mv(session)  # os dados da vítima estão na MV
    return {"d": d.id, "m": m.id, "c": c.id, "q": q.id, "propria": propria.id}


def pdf():
    return {"arquivo": ("a.pdf", criar_pdf(["texto"]), "application/pdf")}


# (método, caminho, corpo) que DEVEM dar 404 para o atacante.
CASOS_404 = [
    ("GET", "/disciplinas/{d}", {}),
    ("PATCH", "/disciplinas/{d}", {"json": {"nome": "hackeada"}}),
    ("DELETE", "/disciplinas/{d}", {}),
    ("GET", "/disciplinas/{d}/materiais", {}),
    ("POST", "/disciplinas/{d}/materiais", {"files": pdf}),
    ("GET", "/disciplinas/{d}/materiais/{m}", {}),
    ("DELETE", "/disciplinas/{d}/materiais/{m}", {}),
    ("POST", "/disciplinas/{d}/materiais/{m}/reprocessar", {}),
    # misturado: a disciplina é do atacante, o material é da vítima
    ("GET", "/disciplinas/{propria}/materiais/{m}", {}),
    ("DELETE", "/disciplinas/{propria}/materiais/{m}", {}),
    ("GET", "/disciplinas/{d}/busca?q=secreto&modo=textual", {}),
    ("POST", "/disciplinas/{d}/perguntar", {"json": {"pergunta": "qual o segredo?"}}),
    ("POST", "/disciplinas/{d}/flashcards/gerar", {"json": {"tema": "segredo"}}),
    ("GET", "/disciplinas/{d}/flashcards", {}),
    ("POST", "/disciplinas/{d}/questoes/gerar", {"json": {"tema": "segredo"}}),
    ("GET", "/disciplinas/{d}/questoes", {}),
    ("POST", "/disciplinas/{d}/questoes/{q}/tentativas", {"json": {"alternativa": "A"}}),
    ("POST", "/disciplinas/{propria}/questoes/{q}/tentativas", {"json": {"alternativa": "A"}}),
    ("POST", "/revisoes/{c}", {"json": {"nota": 5, "versao": 0}}),
    ("GET", "/revisoes/hoje?disciplina_id={d}", {}),
    ("GET", "/analytics/acerto-semanal?disciplina_id={d}", {}),
    ("GET", "/analytics/acerto-semanal?por=material&disciplina_id={d}", {}),
    ("GET", "/analytics/evolucao/diaria?disciplina_id={d}", {}),
    ("GET", "/analytics/evolucao/semanal?disciplina_id={d}", {}),
    ("GET", "/analytics/cards-dificeis?disciplina_id={d}", {}),
    ("GET", "/analytics/sequencia?disciplina_id={d}", {}),
    ("GET", "/analytics/previsao?disciplina_id={d}", {}),
    ("GET", "/analytics/calendario?disciplina_id={d}", {}),
    ("GET", "/analytics/custos?disciplina_id={d}", {}),
]


def _corpo(kwargs):
    return {k: (v() if callable(v) else v) for k, v in kwargs.items()}


@pytest.mark.parametrize(("metodo", "caminho", "kwargs"), CASOS_404,
                         ids=[f"{m} {c}" for m, c, _ in CASOS_404])
def test_ids_da_vitima_dao_404(client, headers, vitima, metodo, caminho, kwargs):
    # anthropic_falso está sem roteiro: se alguma rota chegasse a chamar o LLM com
    # o material da vítima, o teste quebraria (além de dar o status errado).
    resposta = client.request(metodo, caminho.format(**vitima), headers=headers, **_corpo(kwargs))
    assert resposta.status_code == 404, resposta.text


# (método, caminho, verificação) das rotas sem id: não podem trazer nada da vítima.
def _sem_texto_secreto(r):
    assert "secret" not in r.text.lower() and "vítima" not in r.text.lower()


def _sem_respostas(r):
    _sem_texto_secreto(r)
    assert all(x["respostas"] == 0 for x in r.json()["dados"])


CASOS_LISTAS = [
    ("GET", "/disciplinas", _sem_texto_secreto),
    ("POST", "/disciplinas", _sem_texto_secreto),  # cria para o PRÓPRIO usuário
    ("GET", "/gastos", lambda r: _vazio(r.json())),
    ("GET", "/revisoes/hoje", lambda r: _vazio(r.json()["cards"])),
    ("GET", "/auth/eu", _sem_texto_secreto),
    ("GET", "/analytics/acerto-semanal", _sem_respostas),
    ("GET", "/analytics/acerto-semanal?por=material", lambda r: _vazio(r.json()["dados"])),
    ("GET", "/analytics/evolucao/diaria", _sem_respostas),
    ("GET", "/analytics/evolucao/semanal", _sem_respostas),
    ("GET", "/analytics/cards-dificeis", lambda r: _vazio(r.json()["dados"])),
    ("GET", "/analytics/sequencia", lambda r: _zero(r.json()["dias_estudados"])),
    ("GET", "/analytics/previsao", lambda r: _zero(sum(x["cards"] for x in r.json()["dados"]))),
    ("GET", "/analytics/calendario", lambda r: _zero(sum(x["revisoes"] for x in r.json()["dados"]))),
    ("GET", "/analytics/custos", lambda r: _vazio(r.json()["dados"])),
    ("POST", "/analytics/atualizar", _sem_texto_secreto),  # global, não devolve dados
    ("POST", "/auth/sair-de-todos", lambda r: None),  # só afeta o próprio (conferido abaixo)
    ("GET", "/sessoes", lambda r: _vazio(r.json())),
    # Mesma CHAVE e disciplina da vítima: cria uma sessão do atacante, sem disciplina, e
    # não "atualiza" a da vítima (conferido em test_nada_da_vitima_mudou)
    ("POST", "/sessoes/sincronizar",
     lambda r: _sessao_isolada(r.json()["sessoes"][0])),
]


def _sessao_isolada(resultado):
    assert resultado["resultado"] == "criada"
    assert resultado["disciplina_descartada"] is True


def _corpo_da_rota(metodo, caminho, vitima):
    if (metodo, caminho) == ("POST", "/disciplinas"):
        return {"json": {"nome": "Nova minha"}}
    if (metodo, caminho) == ("POST", "/sessoes/sincronizar"):
        return {"json": {"sessoes": [{
            "chave": str(CHAVE_DA_VITIMA), "disciplina_id": vitima["d"], "metodo": "pomodoro",
            "foco_min": 25, "pausa_min": 5, "ciclos": 1, "sistema": "windows",
            "status": "abandonada", "iniciada_em": "2026-10-08T10:00:00+00:00",
            "terminada_em": "2026-10-08T10:05:00+00:00",
        }]}}
    return {}


def _vazio(lista):
    assert lista == []


def _zero(valor):
    assert valor == 0


@pytest.mark.parametrize(("metodo", "caminho", "verificar"), CASOS_LISTAS,
                         ids=[f"{m} {c}" for m, c, _ in CASOS_LISTAS])
def test_rotas_sem_id_nao_mostram_dados_da_vitima(client, headers, vitima, metodo, caminho, verificar):
    resposta = client.request(metodo, caminho, headers=headers, **_corpo_da_rota(metodo, caminho, vitima))
    assert resposta.status_code < 400, resposta.text
    verificar(resposta)


def test_nada_da_vitima_mudou(client, headers, vitima, session, outro_usuario):
    for metodo, caminho, kwargs in CASOS_404:
        client.request(metodo, caminho.format(**vitima), headers=headers, **_corpo(kwargs))
    # Rotas sem id, com o token ainda válido (o "sair de todos" fica por último)
    for metodo, caminho, _ in CASOS_LISTAS:
        if caminho != "/auth/sair-de-todos":
            resposta = client.request(metodo, caminho, headers=headers,
                                      **_corpo_da_rota(metodo, caminho, vitima))
            assert resposta.status_code < 400, (caminho, resposta.text)
    client.post("/auth/sair-de-todos", headers=headers)

    assert session.get(Disciplina, vitima["d"]).nome == "Segredos da vítima"
    assert session.get(Material, vitima["m"]) is not None
    assert session.get(Revisao, vitima["c"]).versao == 0
    assert session.scalar(text("SELECT count(*) FROM tentativas WHERE questao_id = :q"),
                          {"q": vitima["q"]}) == 1
    # a sessão da vítima continua em andamento e na disciplina dela
    linha = session.execute(
        text("SELECT status, disciplina_id FROM sessoes_estudo WHERE chave = :c AND usuario_id = :u"),
        {"c": CHAVE_DA_VITIMA, "u": outro_usuario.id},
    ).one()
    assert tuple(linha) == ("em_andamento", vitima["d"])
    # o "sair de todos" do atacante não derrubou a vítima
    assert client.get("/auth/eu", headers=cabecalho(outro_usuario)).status_code == 200


# ------------------------------------------------------------------ meta-testes


def _rotas_da_api():
    return {(metodo.upper(), caminho)
            for caminho, ops in app.openapi()["paths"].items() for metodo in ops}


def _molde(caminho: str) -> str:
    return caminho.split("?")[0].replace("{propria}", "{disciplina_id}").replace(
        "{d}", "{disciplina_id}").replace("{m}", "{material_id}").replace(
        "{q}", "{questao_id}").replace("{c}", "{flashcard_id}")


def test_toda_rota_tem_caso_de_isolamento():
    cobertas = {(m, _molde(c)) for m, c, _ in CASOS_404} | {(m, _molde(c)) for m, c, _ in CASOS_LISTAS}
    faltando = _rotas_da_api() - PUBLICAS - cobertas
    assert faltando == set(), f"rotas sem teste de isolamento: {sorted(faltando)}"


@pytest.mark.parametrize(("metodo", "caminho"), sorted(_rotas_da_api() - PUBLICAS))
def test_toda_rota_nao_publica_exige_token(client, metodo, caminho):
    concreto = caminho.format(disciplina_id=1, material_id=1, questao_id=1, flashcard_id=1)
    assert client.request(metodo, concreto).status_code == 401
