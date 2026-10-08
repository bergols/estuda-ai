import pytest
from sqlalchemy import func, select

import app.servicos.processamento as processamento
from app.models import Material, Trecho
from app.servicos.chunking import TrechoGerado
from tests.conftest import cabecalho, criar_pdf

PAGINA_1 = "Um índice B-tree mantém as chaves ordenadas. " * 40
PAGINA_2 = "A busca vetorial compara embeddings por distância de cosseno. " * 40


def enviar(client, headers, disciplina_id, conteudo, nome="aula.pdf", tipo="application/pdf"):
    return client.post(
        f"/disciplinas/{disciplina_id}/materiais",
        files={"arquivo": (nome, conteudo, tipo)},
        headers=headers,
    )


def contar_trechos(session, material_id):
    return session.scalar(
        select(func.count()).select_from(Trecho).where(Trecho.material_id == material_id)
    )


# ------------------------------------------------------------ caminho feliz


def test_upload_responde_202_e_processa_em_background(client, headers, disciplina_id, session):
    resposta = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1, PAGINA_2]))

    assert resposta.status_code == 202
    assert resposta.json()["status"] == "pendente"  # resposta sai antes do processamento
    material_id = resposta.json()["id"]

    material = client.get(
        f"/disciplinas/{disciplina_id}/materiais/{material_id}", headers=headers
    ).json()
    assert material["status"] == "concluido"
    assert material["num_paginas"] == 2
    assert material["titulo"] == "aula"
    assert material["processado_em"] is not None

    trechos = session.scalars(
        select(Trecho).where(Trecho.material_id == material_id).order_by(Trecho.ordem)
    ).all()
    assert len(trechos) >= 2
    assert [t.ordem for t in trechos] == list(range(len(trechos)))
    assert trechos[0].pagina == 1 and trechos[-1].pagina_fim == 2
    assert all(t.disciplina_id == disciplina_id for t in trechos)  # cópia desnormalizada
    assert all(len(t.embedding) == 384 for t in trechos)


def test_coluna_gerada_tsvector_e_preenchida_pelo_banco(client, headers, disciplina_id, session):
    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1])).json()["id"]

    tsv = session.scalar(
        select(Trecho.conteudo_tsv).where(Trecho.material_id == material_id).limit(1)
    )
    # sem acento ("indic") e reduzido ao radical ("orden" de "ordenadas")
    assert "'indic'" in tsv and "'orden'" in tsv


def test_arquivo_vai_para_o_volume_com_nome_gerado(client, headers, disciplina_id, pasta_uploads):
    enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1]), nome="../../etc/passwd.pdf")

    arquivos = list(pasta_uploads.iterdir())
    assert len(arquivos) == 1
    assert arquivos[0].suffix == ".pdf" and "passwd" not in arquivos[0].name


# --------------------------------------------------------------- validação


def test_tipo_diferente_de_pdf_retorna_415(client, headers, disciplina_id):
    resposta = enviar(client, headers, disciplina_id, b"ola", nome="a.txt", tipo="text/plain")
    assert resposta.status_code == 415


def test_content_type_pdf_com_conteudo_que_nao_e_pdf_retorna_415(
    client, headers, disciplina_id, pasta_uploads
):
    resposta = enviar(client, headers, disciplina_id, b"<html>nao sou pdf</html>")

    assert resposta.status_code == 415
    assert not any(pasta_uploads.iterdir())  # nada sobra no disco


def test_arquivo_acima_do_limite_retorna_413(client, headers, disciplina_id, pasta_uploads):
    grande = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 1)  # limite nos testes: 1 MB

    resposta = enviar(client, headers, disciplina_id, grande)

    assert resposta.status_code == 413
    assert not any(pasta_uploads.iterdir())


def test_arquivo_vazio_retorna_422(client, headers, disciplina_id):
    assert enviar(client, headers, disciplina_id, b"").status_code == 422


def test_mesmo_arquivo_duas_vezes_retorna_409_e_nao_duplica_no_disco(
    client, headers, disciplina_id, pasta_uploads
):
    pdf = criar_pdf([PAGINA_1])
    enviar(client, headers, disciplina_id, pdf)

    resposta = enviar(client, headers, disciplina_id, pdf, nome="copia.pdf")

    assert resposta.status_code == 409
    assert len(list(pasta_uploads.iterdir())) == 1


def test_upload_em_disciplina_de_outro_usuario_retorna_404(client, disciplina_id, outro_usuario):
    resposta = enviar(
        client, cabecalho(outro_usuario), disciplina_id, criar_pdf([PAGINA_1])
    )
    assert resposta.status_code == 404


# ------------------------------------------------------- falhas e transação


def test_pdf_sem_texto_termina_com_status_erro(client, headers, disciplina_id):
    material = enviar(client, headers, disciplina_id, criar_pdf([""])).json()

    atual = client.get(f"/disciplinas/{disciplina_id}/materiais/{material['id']}", headers=headers)

    assert atual.json()["status"] == "erro"
    assert "texto extraível" in atual.json()["erro_mensagem"]


def test_falha_no_meio_da_gravacao_nao_deixa_trechos_pela_metade(
    client, headers, disciplina_id, session, monkeypatch
):
    """O último trecho repete a 'ordem' do penúltimo e viola o UNIQUE
    (material_id, ordem). Os trechos válidos anteriores também não podem ficar."""
    dividir_original = processamento.dividir_em_trechos

    def dividir_com_defeito(*args, **kwargs):
        trechos = dividir_original(*args, **kwargs)
        ultimo = trechos[-1]
        defeituoso = TrechoGerado(
            ordem=trechos[-2].ordem,  # duplicada!
            conteudo=ultimo.conteudo,
            pagina=ultimo.pagina,
            pagina_fim=ultimo.pagina_fim,
            num_tokens=ultimo.num_tokens,
        )
        return [*trechos[:-1], defeituoso]

    monkeypatch.setattr(processamento, "dividir_em_trechos", dividir_com_defeito)

    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1, PAGINA_2])).json()[
        "id"
    ]

    material = session.get(Material, material_id)
    session.refresh(material)
    assert material.status == "erro"
    assert "uq_trechos_material_id_ordem" in material.erro_mensagem
    assert contar_trechos(session, material_id) == 0  # ROLLBACK desfez os válidos também


def test_reprocessar_material_com_erro(client, headers, disciplina_id, embedder, session):
    embedder.falhar_apos = 0  # o modelo "cai" na primeira chamada
    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1])).json()["id"]
    url = f"/disciplinas/{disciplina_id}/materiais/{material_id}"
    assert client.get(url, headers=headers).json()["status"] == "erro"

    embedder.falhar_apos = None  # o modelo "volta"
    resposta = client.post(f"{url}/reprocessar", headers=headers)

    assert resposta.status_code == 202
    atual = client.get(url, headers=headers).json()
    assert atual["status"] == "concluido"
    assert atual["erro_mensagem"] is None  # o CHECK exige: só há mensagem com status 'erro'
    assert contar_trechos(session, material_id) > 0


def test_reprocessar_material_concluido_retorna_409(client, headers, disciplina_id):
    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1])).json()["id"]

    resposta = client.post(
        f"/disciplinas/{disciplina_id}/materiais/{material_id}/reprocessar", headers=headers
    )

    assert resposta.status_code == 409


def test_processar_de_novo_um_material_ja_concluido_nao_faz_nada(
    client, headers, disciplina_id, session, fabrica, embedder, pasta_uploads
):
    """A reserva é um compare-and-set (WHERE status = 'pendente')."""
    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1])).json()["id"]
    antes = contar_trechos(session, material_id)

    processamento.processar_material(material_id, pasta_uploads, fabrica, embedder)

    assert contar_trechos(session, material_id) == antes  # nada duplicado


# ----------------------------------------------------------- listar/apagar


def test_listar_materiais_da_disciplina(client, headers, disciplina_id):
    enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1]), nome="a.pdf")
    enviar(client, headers, disciplina_id, criar_pdf([PAGINA_2]), nome="b.pdf")

    materiais = client.get(f"/disciplinas/{disciplina_id}/materiais", headers=headers).json()

    assert {m["titulo"] for m in materiais} == {"a", "b"}


def test_apagar_material_remove_trechos_e_arquivo(
    client, headers, disciplina_id, session, pasta_uploads
):
    material_id = enviar(client, headers, disciplina_id, criar_pdf([PAGINA_1])).json()["id"]

    resposta = client.delete(f"/disciplinas/{disciplina_id}/materiais/{material_id}", headers=headers)

    assert resposta.status_code == 204
    assert contar_trechos(session, material_id) == 0
    assert not any(pasta_uploads.iterdir())


@pytest.mark.parametrize("material_id", [999999])
def test_material_inexistente_retorna_404(client, headers, disciplina_id, material_id):
    url = f"/disciplinas/{disciplina_id}/materiais/{material_id}"
    assert client.get(url, headers=headers).status_code == 404
