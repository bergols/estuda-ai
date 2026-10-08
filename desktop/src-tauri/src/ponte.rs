//! Ponte página → API: os comandos que o frontend chama com `invoke(...)`.
//!
//! Caminho de uma chamada no app desktop:
//!
//!   página (openapi-fetch) → invoke("chamar_api") → Rust: valida o caminho, lê o token
//!   do cofre, chama https://<BFF>/api/<caminho> com "Authorization: Bearer" → BFF na
//!   Vercel → API
//!
//! Passar pelo BFF (e não direto pela API) mantém um único portão público: o Caddy
//! continua recusando quem não traz o segredo do BFF, e o desktop não precisa
//! carregar segredo nenhum dentro do instalador. O Rust faz a requisição (e não o
//! `fetch` da página) para o token não precisar existir no JavaScript, e porque um
//! cliente nativo não está sujeito a CORS.

use std::time::Duration;

use nucleo::api::{ErroUrl, base_da_api, url_da_chamada};
use reqwest::{Method, StatusCode};
use serde::{Deserialize, Serialize};
use tauri::State;
use tauri::ipc::{InvokeBody, Request};
use url::Url;

use crate::cofre;

/// Servidor usado pelo app: fixado na compilação (variável ESTUDA_AI_URL no build, ou
/// o padrão abaixo). Em build de desenvolvimento, a mesma variável lida na hora de
/// rodar tem prioridade, para apontar para o `next dev` local.
const URL_PADRAO: &str = "https://estuda-ai-jade.vercel.app";

pub struct Estado {
    http: reqwest::Client,
    base: Url,
}

impl Estado {
    pub fn novo() -> Result<Self, String> {
        let em_tempo_de_execucao = if cfg!(debug_assertions) {
            std::env::var("ESTUDA_AI_URL").ok()
        } else {
            None
        };
        // Vazia conta como não definida (no CI, uma variável de repositório ausente
        // chega como "" e não pode virar uma URL inválida dentro do instalador)
        let endereco = em_tempo_de_execucao
            .filter(|s| !s.is_empty())
            .or(option_env!("ESTUDA_AI_URL")
                .filter(|s| !s.is_empty())
                .map(str::to_owned))
            .unwrap_or_else(|| URL_PADRAO.to_owned());
        Self::com_endereco(&endereco)
    }

    pub fn com_endereco(endereco: &str) -> Result<Self, String> {
        let base = base_da_api(endereco).map_err(|e| format!("ESTUDA_AI_URL inválida: {e:?}"))?;
        let http = reqwest::Client::builder()
            // Gerações com IA levam dezenas de segundos (o BFF espera até 120 s)
            .timeout(Duration::from_secs(130))
            .connect_timeout(Duration::from_secs(10))
            // Redirecionamento poderia levar o token a outro host: nunca seguir
            .redirect(reqwest::redirect::Policy::none())
            .user_agent(concat!("estuda-ai-desktop/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Self { http, base })
    }

    fn origem(&self) -> String {
        self.base.origin().ascii_serialization()
    }
}

/// O que a página recebe de volta; `api.ts` transforma isto num `Response`.
#[derive(Serialize)]
pub struct RespostaApi {
    status: u16,
    tipo: Option<String>,
    retry_after: Option<String>,
    corpo: String,
}

impl RespostaApi {
    fn erro(status: StatusCode, detalhe: &str) -> Self {
        Self {
            status: status.as_u16(),
            tipo: Some("application/json".into()),
            retry_after: None,
            corpo: serde_json::json!({ "detail": detalhe }).to_string(),
        }
    }
}

/// Repassa uma chamada da página para a API (o equivalente do /api/[...caminho] da web).
///
/// Recebe o corpo cru (bytes) e metadados em cabeçalhos do IPC: assim o upload de PDF
/// (multipart) passa sem conversões, com o boundary no content-type.
#[tauri::command]
pub async fn chamar_api(
    request: Request<'_>,
    estado: State<'_, Estado>,
) -> Result<RespostaApi, String> {
    let cabecalho = |nome: &str| {
        request
            .headers()
            .get(nome)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned)
    };
    let corpo = match request.body() {
        InvokeBody::Raw(bytes) => bytes.clone(),
        InvokeBody::Json(_) => Vec::new(),
    };
    chamar(
        &estado,
        cabecalho("x-metodo").as_deref().unwrap_or_default(),
        cabecalho("x-caminho").as_deref().unwrap_or_default(),
        cabecalho("content-type").filter(|t| !t.is_empty()),
        corpo,
    )
    .await
}

/// O repasse em si, separado do comando para ser testado sem janela (testes abaixo).
pub(crate) async fn chamar(
    estado: &Estado,
    metodo: &str,
    caminho: &str,
    tipo: Option<String>,
    corpo: Vec<u8>,
) -> Result<RespostaApi, String> {
    let metodo = match metodo {
        "GET" => Method::GET,
        "POST" => Method::POST,
        "PATCH" => Method::PATCH,
        "PUT" => Method::PUT,
        "DELETE" => Method::DELETE,
        _ => {
            return Ok(RespostaApi::erro(
                StatusCode::METHOD_NOT_ALLOWED,
                "método não permitido",
            ));
        }
    };
    let url = match url_da_chamada(&estado.base, caminho) {
        Ok(url) => url,
        Err(ErroUrl::CaminhoRecusado | ErroUrl::BaseInvalida(_)) => {
            return Ok(RespostaApi::erro(StatusCode::NOT_FOUND, "não encontrado"));
        }
    };

    let origem = estado.origem();
    let Some(token) = cofre::ler(&origem)? else {
        return Ok(RespostaApi::erro(
            StatusCode::UNAUTHORIZED,
            "sessão expirada",
        ));
    };

    let mut pedido = estado
        .http
        .request(metodo.clone(), url)
        .bearer_auth(token)
        .header("Accept", "application/json");
    if metodo != Method::GET {
        if let Some(tipo) = tipo {
            pedido = pedido.header("Content-Type", tipo);
        }
        pedido = pedido.body(corpo);
    }

    let resposta = match pedido.send().await {
        Ok(r) => r,
        Err(e) => return Ok(falha_de_rede(&e)),
    };
    // Token expirado ou revogado ("sair de todos"): não serve mais, sai do cofre.
    if resposta.status() == StatusCode::UNAUTHORIZED {
        cofre::apagar(&origem)?;
    }
    Ok(converter(resposta).await)
}

#[derive(Deserialize)]
struct TokenDaApi {
    access_token: String,
}

/// Login: troca e-mail e senha por um token, guardado direto no cofre.
/// A página só fica sabendo se deu certo; o token nunca volta para ela.
#[tauri::command]
pub async fn entrar(
    email: String,
    senha: String,
    estado: State<'_, Estado>,
) -> Result<RespostaApi, String> {
    entrar_com(&estado, &email, &senha).await
}

pub(crate) async fn entrar_com(
    estado: &Estado,
    email: &str,
    senha: &str,
) -> Result<RespostaApi, String> {
    let url = estado.base.join("api/token").map_err(|e| e.to_string())?;
    let resposta = match estado
        .http
        .post(url)
        .json(&serde_json::json!({ "email": email, "senha": senha }))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => return Ok(falha_de_rede(&e)),
    };
    if !resposta.status().is_success() {
        return Ok(converter(resposta).await);
    }
    let token: TokenDaApi = resposta
        .json()
        .await
        .map_err(|e| format!("resposta inesperada do servidor: {e}"))?;
    cofre::gravar(&estado.origem(), &token.access_token)?;
    Ok(RespostaApi {
        status: 204,
        tipo: None,
        retry_after: None,
        corpo: String::new(),
    })
}

/// Sair deste computador: apaga o token do cofre. (Para derrubar todos os aparelhos,
/// a página chama auth/sair-de-todos antes, como na web.)
#[tauri::command]
pub fn sair(estado: State<'_, Estado>) -> Result<(), String> {
    sair_de(&estado)
}

pub(crate) fn sair_de(estado: &Estado) -> Result<(), String> {
    cofre::apagar(&estado.origem())
}

async fn converter(resposta: reqwest::Response) -> RespostaApi {
    let ler = |nome: &str| {
        resposta
            .headers()
            .get(nome)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned)
    };
    let status = resposta.status().as_u16();
    let tipo = ler("content-type");
    let retry_after = ler("retry-after");
    let corpo = resposta.text().await.unwrap_or_default();
    RespostaApi {
        status,
        tipo,
        retry_after,
        corpo,
    }
}

/// Sem internet, servidor fora do ar, DNS: um 503 com mensagem clara (a página já sabe
/// mostrar). Na sessão de estudos (próxima etapa), é isto que liga o modo offline.
fn falha_de_rede(erro: &reqwest::Error) -> RespostaApi {
    let detalhe = if erro.is_timeout() {
        "o servidor demorou demais para responder; tente de novo"
    } else {
        "sem conexão com o servidor do estuda-ai (internet ou servidor fora do ar)"
    };
    eprintln!("ponte: falha de rede: {erro}");
    RespostaApi::erro(StatusCode::SERVICE_UNAVAILABLE, detalhe)
}

#[cfg(test)]
mod testes {
    use super::*;

    /// Fluxo real, de ponta a ponta, contra o `next dev` local (BFF na porta 3000, API
    /// e banco de desenvolvimento) e o cofre DE VERDADE do sistema. Fica fora da
    /// execução padrão (precisa da pilha local e mexe no Keychain):
    ///
    ///   ESTUDA_AI_TESTE_SENHA=... cargo test -p estuda-ai -- --ignored
    #[tokio::test]
    #[ignore]
    async fn login_cofre_chamada_e_saida() {
        let senha = std::env::var("ESTUDA_AI_TESTE_SENHA").expect("defina ESTUDA_AI_TESTE_SENHA");
        let estado = Estado::com_endereco("http://localhost:3000").unwrap();
        sair_de(&estado).unwrap();

        // Sem token no cofre: 401 sem nem chamar o servidor
        let r = chamar(&estado, "GET", "auth/eu", None, vec![])
            .await
            .unwrap();
        assert_eq!(r.status, 401);

        // Senha errada: o erro da API volta como veio
        let r = entrar_com(&estado, "estudante@estuda-ai.local", "errada123")
            .await
            .unwrap();
        assert_eq!(r.status, 401);
        assert_eq!(cofre::ler(&estado.origem()).unwrap(), None);

        // Login: 204 para a página, token só no cofre
        let r = entrar_com(&estado, "estudante@estuda-ai.local", &senha)
            .await
            .unwrap();
        assert_eq!(r.status, 204);
        assert!(r.corpo.is_empty(), "o token não pode voltar para a página");
        assert!(cofre::ler(&estado.origem()).unwrap().is_some());

        // Chamada autenticada, com busca codificada
        let r = chamar(&estado, "GET", "auth/eu", None, vec![])
            .await
            .unwrap();
        assert_eq!(r.status, 200, "{}", r.corpo);
        assert!(r.corpo.contains("estudante@estuda-ai.local"));
        let r = chamar(&estado, "GET", "disciplinas?limite=5", None, vec![])
            .await
            .unwrap();
        assert_eq!(r.status, 200, "{}", r.corpo);

        // POST com corpo JSON (validação da API: 422 prova que o corpo chegou)
        let r = chamar(
            &estado,
            "POST",
            "disciplinas",
            Some("application/json".into()),
            b"{}".to_vec(),
        )
        .await
        .unwrap();
        assert_eq!(r.status, 422, "{}", r.corpo);

        // Fora da allowlist: recusado aqui, sem sair do computador
        let r = chamar(&estado, "GET", "disciplinas/../docs", None, vec![])
            .await
            .unwrap();
        assert_eq!(r.status, 404);

        // Sair: token apagado, próxima chamada 401
        sair_de(&estado).unwrap();
        let r = chamar(&estado, "GET", "auth/eu", None, vec![])
            .await
            .unwrap();
        assert_eq!(r.status, 401);
    }

    #[tokio::test]
    async fn servidor_fora_do_ar_vira_503_com_mensagem() {
        // Porta 9 (discard) em localhost: conexão recusada na hora
        let estado = Estado::com_endereco("http://127.0.0.1:9").unwrap();
        let r = entrar_com(&estado, "a@b.c", "12345678").await.unwrap();
        assert_eq!(r.status, 503);
        assert!(r.corpo.contains("sem conexão"));
    }
}
