//! Regras do Spotify que não dependem de rede nem de janela: PKCE, a URL de
//! autorização, a leitura do retorno do navegador, a escolha do dispositivo e a
//! classificação dos erros do player. A parte que fala com a rede fica em
//! src-tauri/src/spotify.rs.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use percent_encoding::{NON_ALPHANUMERIC, percent_decode_str, utf8_percent_encode};
use serde::Deserialize;
use sha2::{Digest, Sha256};

/// Porta fixa do retorno (cadastrada no app do Spotify como
/// http://127.0.0.1:43821/callback). O Spotify aceitaria porta dinâmica em loopback,
/// mas a porta fixa funciona tanto com a regra exata quanto com a flexível.
pub const PORTA_RETORNO: u16 = 43821;

pub fn redirect_uri() -> String {
    format!("http://127.0.0.1:{PORTA_RETORNO}/callback")
}

// ------------------------------------------------------------------- PKCE

/// code_verifier (RFC 7636): segredo aleatório que NUNCA sai do app até a troca do
/// código. 64 bytes aleatórios em base64url = 86 caracteres (o limite é 43 a 128).
pub fn verifier(aleatorio: &[u8; 64]) -> String {
    URL_SAFE_NO_PAD.encode(aleatorio)
}

/// code_challenge = base64url(SHA-256(verifier)). É isto que vai na URL do navegador:
/// quem ver a URL (ou interceptar o `code` no retorno) não consegue o verifier, e sem
/// ele o Spotify não troca o código por tokens.
pub fn challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

/// `state`: valor aleatório que vai na URL e precisa voltar igual no retorno. Protege o
/// "callback" de ser chamado por um site qualquer com um código de OUTRA conta (CSRF
/// do OAuth).
pub fn state(aleatorio: &[u8; 16]) -> String {
    URL_SAFE_NO_PAD.encode(aleatorio)
}

fn codificar(valor: &str) -> String {
    utf8_percent_encode(valor, NON_ALPHANUMERIC).to_string()
}

pub fn url_autorizacao(client_id: &str, escopos: &str, challenge: &str, state: &str) -> String {
    format!(
        "https://accounts.spotify.com/authorize?response_type=code&client_id={}&scope={}\
         &redirect_uri={}&code_challenge_method=S256&code_challenge={}&state={}",
        codificar(client_id),
        codificar(escopos),
        codificar(&redirect_uri()),
        codificar(challenge),
        codificar(state),
    )
}

#[derive(Debug, PartialEq, Eq)]
pub enum ErroRetorno {
    /// O usuário clicou em "Cancelar" no Spotify (error=access_denied)
    Negado,
    /// state diferente do enviado: o retorno não é da autorização que começamos
    StateDiferente,
    /// Pedido que não é o retorno (favicon, outra rota, sem code)
    NaoERetorno,
}

/// Lê a primeira linha do pedido HTTP que o navegador fez ao app
/// ("GET /callback?code=...&state=... HTTP/1.1") e devolve o `code`.
pub fn ler_retorno(linha: &str, state_esperado: &str) -> Result<String, ErroRetorno> {
    let mut partes = linha.split_whitespace();
    let (Some("GET"), Some(alvo)) = (partes.next(), partes.next()) else {
        return Err(ErroRetorno::NaoERetorno);
    };
    let (caminho, busca) = alvo.split_once('?').unwrap_or((alvo, ""));
    if caminho != "/callback" {
        return Err(ErroRetorno::NaoERetorno);
    }
    let mut code = None;
    let mut state = None;
    let mut erro = None;
    for par in busca.split('&') {
        let (chave, valor) = par.split_once('=').unwrap_or((par, ""));
        let valor = percent_decode_str(&valor.replace('+', " "))
            .decode_utf8_lossy()
            .into_owned();
        match chave {
            "code" => code = Some(valor),
            "state" => state = Some(valor),
            "error" => erro = Some(valor),
            _ => {}
        }
    }
    // O state é conferido ANTES de olhar o erro ou o código
    if state.as_deref() != Some(state_esperado) {
        return Err(ErroRetorno::StateDiferente);
    }
    if erro.is_some() {
        return Err(ErroRetorno::Negado);
    }
    code.filter(|c| !c.is_empty())
        .ok_or(ErroRetorno::NaoERetorno)
}

// -------------------------------------------------------------- dispositivo

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Dispositivo {
    pub id: Option<String>,
    pub name: String,
    #[serde(rename = "type")]
    pub tipo: String,
    #[serde(default)]
    pub is_active: bool,
    #[serde(default)]
    pub is_restricted: bool,
}

/// Onde tocar: o Spotify DESTE computador. Prioridade: um computador já ativo; o
/// computador com o nome desta máquina (o app do Spotify usa o nome do computador);
/// qualquer computador. Nunca escolhe o celular ou a caixa de som: "estudar no
/// computador" não deve começar a tocar no quarto ao lado.
pub fn escolher_dispositivo<'a>(
    dispositivos: &'a [Dispositivo],
    nome_do_computador: &str,
) -> Option<&'a Dispositivo> {
    let usaveis: Vec<&Dispositivo> = dispositivos
        .iter()
        .filter(|d| d.id.is_some() && !d.is_restricted && d.tipo.eq_ignore_ascii_case("computer"))
        .collect();
    let mesmo_nome = |d: &&&Dispositivo| {
        d.name.eq_ignore_ascii_case(nome_do_computador)
            || d.name
                .to_lowercase()
                .starts_with(&nome_do_computador.to_lowercase())
    };
    usaveis
        .iter()
        .find(|d| d.is_active && mesmo_nome(d))
        .or_else(|| usaveis.iter().find(|d| mesmo_nome(d)))
        .or_else(|| usaveis.iter().find(|d| d.is_active))
        .or_else(|| usaveis.first())
        .copied()
}

// ------------------------------------------------------------------- erros

/// Erro do player já classificado: a tela mostra a mensagem certa para cada caso.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum ErroPlayer {
    /// 401: o access token venceu ou foi revogado antes da hora (renovar e repetir)
    TokenExpirado,
    /// Nenhum Spotify aberto em computador nenhum (o app tenta abrir o deste)
    SpotifyFechado,
    /// 404 NO_ACTIVE_DEVICE: há dispositivo, mas nenhum ativo (escolher e repetir)
    SemDispositivoAtivo,
    /// 403 PREMIUM_REQUIRED: o controle remoto do player é só para Premium
    SemPremium,
    /// Outro 403 ("Restriction violated"): ex. pausar o que já está pausado
    Recusado,
    /// 429: espere `espera_s` (cota = a cota diária do app acabou, não adianta insistir)
    Limite {
        espera_s: u64,
        cota: bool,
    },
    /// O Spotify do backend não está conectado (ou foi revogado)
    NaoConectado,
    /// Sem internet ou servidor fora do ar
    SemConexao,
    Outro {
        mensagem: String,
    },
}

#[derive(Deserialize, Default)]
struct CorpoErro {
    #[serde(default)]
    error: Option<DetalheErro>,
    #[serde(default)]
    reason: Option<String>,
}

#[derive(Deserialize, Default)]
struct DetalheErro {
    #[serde(default)]
    message: Option<String>,
    #[serde(default)]
    reason: Option<String>,
}

/// Classifica a resposta de erro da Web API do Spotify.
pub fn classificar(status: u16, corpo: &str, retry_after: Option<&str>) -> ErroPlayer {
    let corpo: CorpoErro = serde_json::from_str(corpo).unwrap_or_default();
    let detalhe = corpo.error.unwrap_or_default();
    let razao = corpo.reason.or(detalhe.reason).unwrap_or_default();
    match status {
        401 => ErroPlayer::TokenExpirado,
        403 if razao == "PREMIUM_REQUIRED" => ErroPlayer::SemPremium,
        403 => ErroPlayer::Recusado,
        404 if razao == "NO_ACTIVE_DEVICE" => ErroPlayer::SemDispositivoAtivo,
        429 => ErroPlayer::Limite {
            espera_s: retry_after
                .and_then(|r| r.trim().parse().ok())
                .unwrap_or(30),
            cota: razao == "QUOTA_EXCEEDED",
        },
        500..=599 => ErroPlayer::Outro {
            mensagem: "o Spotify está com problemas agora".into(),
        },
        _ => ErroPlayer::Outro {
            mensagem: detalhe
                .message
                .unwrap_or_else(|| format!("o Spotify respondeu {status}")),
        },
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn challenge_bate_com_o_exemplo_da_rfc_7636() {
        // Apêndice B da RFC 7636
        assert_eq!(
            challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn verifier_no_tamanho_e_alfabeto_da_rfc() {
        let v = verifier(&[0xAB; 64]);
        assert_eq!(v.len(), 86);
        assert!(
            v.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        );
    }

    #[test]
    fn url_de_autorizacao() {
        let url = url_autorizacao(
            "abc123",
            "user-read-playback-state playlist-read-private",
            "CH",
            "ST",
        );
        assert!(url.starts_with(
            "https://accounts.spotify.com/authorize?response_type=code&client_id=abc123"
        ));
        assert!(url.contains("&scope=user%2Dread%2Dplayback%2Dstate%20playlist%2Dread%2Dprivate"));
        assert!(url.contains("&redirect_uri=http%3A%2F%2F127%2E0%2E0%2E1%3A43821%2Fcallback"));
        assert!(url.contains("&code_challenge_method=S256&code_challenge=CH&state=ST"));
    }

    #[test]
    fn retorno_com_codigo() {
        let linha = "GET /callback?code=AQB%2Dx_y&state=abc HTTP/1.1";
        assert_eq!(ler_retorno(linha, "abc"), Ok("AQB-x_y".into()));
    }

    #[test]
    fn retorno_com_state_errado_e_recusado_antes_de_tudo() {
        let linha = "GET /callback?code=xyz&state=OUTRO HTTP/1.1";
        assert_eq!(ler_retorno(linha, "abc"), Err(ErroRetorno::StateDiferente));
        let sem_state = "GET /callback?code=xyz HTTP/1.1";
        assert_eq!(
            ler_retorno(sem_state, "abc"),
            Err(ErroRetorno::StateDiferente)
        );
    }

    #[test]
    fn usuario_cancelou() {
        let linha = "GET /callback?error=access_denied&state=abc HTTP/1.1";
        assert_eq!(ler_retorno(linha, "abc"), Err(ErroRetorno::Negado));
    }

    #[test]
    fn pedidos_que_nao_sao_o_retorno() {
        for linha in [
            "GET /favicon.ico HTTP/1.1",
            "POST /callback?code=x&state=abc HTTP/1.1",
            "GET /callback?state=abc HTTP/1.1",
            "",
        ] {
            assert_eq!(
                ler_retorno(linha, "abc"),
                Err(ErroRetorno::NaoERetorno),
                "{linha}"
            );
        }
    }

    fn d(id: &str, nome: &str, tipo: &str, ativo: bool) -> Dispositivo {
        Dispositivo {
            id: Some(id.into()),
            name: nome.into(),
            tipo: tipo.into(),
            is_active: ativo,
            is_restricted: false,
        }
    }

    #[test]
    fn escolhe_este_computador_e_nunca_o_celular() {
        let lista = [
            d("cel", "iPhone", "Smartphone", true),
            d("win", "PC-DO-QUARTO", "Computer", false),
            d("mac", "MacBook-Air-de-Joao", "Computer", false),
        ];
        assert_eq!(
            escolher_dispositivo(&lista, "macbook-air-de-joao")
                .unwrap()
                .id
                .as_deref(),
            Some("mac")
        );
        // Sem computador com este nome: um computador qualquer, ainda não o celular
        assert_eq!(
            escolher_dispositivo(&lista, "outro").unwrap().id.as_deref(),
            Some("win")
        );
        // Só celular: ninguém
        assert_eq!(escolher_dispositivo(&lista[..1], "mac"), None);
    }

    #[test]
    fn computador_ativo_com_o_nome_vence() {
        let lista = [
            d("a", "Mac", "Computer", false),
            d("b", "Mac", "Computer", true),
        ];
        assert_eq!(
            escolher_dispositivo(&lista, "Mac").unwrap().id.as_deref(),
            Some("b")
        );
    }

    #[test]
    fn dispositivo_restrito_ou_sem_id_fica_de_fora() {
        let mut restrito = d("x", "Mac", "Computer", true);
        restrito.is_restricted = true;
        let sem_id = Dispositivo {
            id: None,
            ..d("y", "Mac", "Computer", true)
        };
        assert_eq!(escolher_dispositivo(&[restrito, sem_id], "Mac"), None);
    }

    #[test]
    fn classificacao_dos_erros() {
        assert_eq!(classificar(401, "{}", None), ErroPlayer::TokenExpirado);
        assert_eq!(
            classificar(
                404,
                r#"{"error":{"status":404,"message":"Player command failed: No active device found","reason":"NO_ACTIVE_DEVICE"}}"#,
                None
            ),
            ErroPlayer::SemDispositivoAtivo
        );
        assert_eq!(
            classificar(
                403,
                r#"{"error":{"status":403,"message":"x","reason":"PREMIUM_REQUIRED"}}"#,
                None
            ),
            ErroPlayer::SemPremium
        );
        assert_eq!(
            classificar(429, "", Some("12")),
            ErroPlayer::Limite {
                espera_s: 12,
                cota: false
            }
        );
        assert_eq!(
            classificar(
                429,
                r#"{"error":{"status":429,"reason":"QUOTA_EXCEEDED"}}"#,
                Some("3600")
            ),
            ErroPlayer::Limite {
                espera_s: 3600,
                cota: true
            }
        );
        assert_eq!(
            classificar(429, "", None),
            ErroPlayer::Limite {
                espera_s: 30,
                cota: false
            }
        );
        assert_eq!(
            classificar(
                403,
                r#"{"error":{"status":403,"message":"Player command failed: Restriction violated","reason":"UNKNOWN"}}"#,
                None
            ),
            ErroPlayer::Recusado
        );
        assert!(matches!(
            classificar(503, "<html>", None),
            ErroPlayer::Outro { .. }
        ));
        assert_eq!(
            classificar(
                400,
                r#"{"error":{"status":400,"message":"Invalid context uri"}}"#,
                None
            ),
            ErroPlayer::Outro {
                mensagem: "Invalid context uri".into()
            }
        );
    }
}
