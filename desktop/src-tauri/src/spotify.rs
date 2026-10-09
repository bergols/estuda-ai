//! Spotify no app desktop: conectar a conta (PKCE com retorno em 127.0.0.1) e
//! controlar o player do Spotify DESTE computador durante a sessão de estudo.
//!
//! O access token vem do backend (POST /api/spotify/token, que renova sozinho); o
//! refresh token nunca chega ao computador. As chamadas ao player vão direto do Rust
//! para api.spotify.com: são do usuário, sobre o dispositivo dele, e não precisam
//! passar pelo nosso servidor. Regras puras (PKCE, dispositivo, erros) em nucleo::spotify.

use std::future::Future;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::pin::Pin;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use nucleo::spotify::{
    self as regras, Dispositivo, ErroPlayer, ErroRetorno, PORTA_RETORNO, escolher_dispositivo,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::{AppHandle, State};
use tauri_plugin_opener::OpenerExt;

use crate::ponte::{self, Estado};

const API: &str = "https://api.spotify.com/v1";
/// Quanto esperar o Spotify recém-aberto aparecer como dispositivo
const ESPERA_ABRIR: Duration = Duration::from_secs(15);

// ------------------------------------------------------------ fonte do token

type Futuro<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// De onde vem o access token. No app, do backend; nos testes, de um valor fixo.
pub trait FonteToken: Sync {
    fn token(&self, forcar: bool) -> Futuro<'_, Result<String, ErroPlayer>>;
}

/// O backend (pelo BFF, com o login guardado no cofre).
pub struct FonteBff<'a>(pub &'a Estado);

#[derive(Deserialize)]
struct TokenDoBackend {
    access_token: String,
}

impl FonteToken for FonteBff<'_> {
    fn token(&self, forcar: bool) -> Futuro<'_, Result<String, ErroPlayer>> {
        Box::pin(async move {
            let corpo = json!({ "forcar": forcar }).to_string().into_bytes();
            let r = ponte::chamar(
                self.0,
                "POST",
                "spotify/token",
                Some("application/json".into()),
                corpo,
            )
            .await
            .map_err(|e| ErroPlayer::Outro { mensagem: e })?;
            match r.status() {
                200 => serde_json::from_str::<TokenDoBackend>(r.corpo())
                    .map(|t| t.access_token)
                    .map_err(|_| ErroPlayer::Outro {
                        mensagem: "resposta inesperada do servidor".into(),
                    }),
                // 404: não conectado; 409: o Spotify revogou (o backend já desconectou)
                404 | 409 => Err(ErroPlayer::NaoConectado),
                429 => Err(ErroPlayer::Limite {
                    espera_s: 60,
                    cota: false,
                }),
                503 => Err(ErroPlayer::SemConexao),
                _ => Err(ErroPlayer::Outro {
                    mensagem: r.detalhe(),
                }),
            }
        })
    }
}

// -------------------------------------------------------------------- player

pub struct Player {
    http: reqwest::Client,
    base: String,
    nome_do_computador: String,
    token: tokio::sync::Mutex<Option<String>>,
    /// Depois de um 429: não chama o Spotify até este instante (insistir só piora)
    espera_ate: Mutex<Option<Instant>>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Tocando {
    pub tocando: bool,
    pub musica: String,
    pub artistas: String,
    pub capa: Option<String>,
    pub progresso_ms: u64,
    pub duracao_ms: u64,
    /// Volume do dispositivo que está tocando (None se ele não deixa mudar)
    pub volume: Option<u8>,
}

impl Player {
    pub fn novo() -> Self {
        Self::com_base(API, &gethostname::gethostname().to_string_lossy())
    }

    pub fn com_base(base: &str, nome_do_computador: &str) -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(10))
                .build()
                .expect("cliente HTTP"),
            base: base.trim_end_matches('/').to_owned(),
            nome_do_computador: nome_do_computador.to_owned(),
            token: tokio::sync::Mutex::new(None),
            espera_ate: Mutex::new(None),
        }
    }

    /// Uma chamada à Web API com as regras de erro: 401 renova o token UMA vez e repete;
    /// 429 liga a espera (Retry-After) e as próximas chamadas nem saem do computador.
    async fn chamar(
        &self,
        fonte: &dyn FonteToken,
        metodo: reqwest::Method,
        caminho: &str,
        corpo: Option<&Value>,
    ) -> Result<(u16, String), ErroPlayer> {
        if let Some(ate) = *self.espera_ate.lock().unwrap_or_else(|e| e.into_inner()) {
            let agora = Instant::now();
            if agora < ate {
                return Err(ErroPlayer::Limite {
                    espera_s: (ate - agora).as_secs().max(1),
                    cota: false,
                });
            }
        }
        let mut renovado = false;
        loop {
            let token = {
                let mut guardado = self.token.lock().await;
                match guardado.clone() {
                    Some(t) if !renovado => t,
                    _ => {
                        let novo = fonte.token(renovado).await?;
                        *guardado = Some(novo.clone());
                        novo
                    }
                }
            };
            let mut pedido = self
                .http
                .request(metodo.clone(), format!("{}{caminho}", self.base))
                .bearer_auth(&token);
            pedido = match corpo {
                Some(c) => pedido.json(c),
                // PUT sem corpo ainda precisa de Content-Length (o Spotify exige)
                None if metodo != reqwest::Method::GET => pedido.header("Content-Length", "0"),
                None => pedido,
            };
            let resposta = pedido.send().await.map_err(|_| ErroPlayer::SemConexao)?;
            let status = resposta.status().as_u16();
            let retry = resposta
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .map(str::to_owned);
            let texto = resposta.text().await.unwrap_or_default();
            if (200..300).contains(&status) {
                return Ok((status, texto));
            }
            match regras::classificar(status, &texto, retry.as_deref()) {
                ErroPlayer::TokenExpirado if !renovado => renovado = true, // repete com token novo
                erro @ ErroPlayer::Limite { espera_s, .. } => {
                    *self.espera_ate.lock().unwrap_or_else(|e| e.into_inner()) =
                        Some(Instant::now() + Duration::from_secs(espera_s));
                    return Err(erro);
                }
                erro => return Err(erro),
            }
        }
    }

    /// O id do Spotify deste computador, ou SpotifyFechado.
    async fn dispositivo(&self, fonte: &dyn FonteToken) -> Result<String, ErroPlayer> {
        #[derive(Deserialize)]
        struct Lista {
            devices: Vec<Dispositivo>,
        }
        let (_, corpo) = self
            .chamar(fonte, reqwest::Method::GET, "/me/player/devices", None)
            .await?;
        let lista: Lista = serde_json::from_str(&corpo).unwrap_or(Lista { devices: vec![] });
        escolher_dispositivo(&lista.devices, &self.nome_do_computador)
            .and_then(|d| d.id.clone())
            .ok_or(ErroPlayer::SpotifyFechado)
    }

    /// Toca uma playlist/álbum no Spotify deste computador. Spotify fechado: chama
    /// `abrir` (abre o app) e espera ele aparecer como dispositivo.
    pub async fn tocar(
        &self,
        fonte: &dyn FonteToken,
        contexto: &str,
        abrir: &(dyn Fn() + Sync),
        espera: Duration,
    ) -> Result<(), ErroPlayer> {
        let id = match self.dispositivo(fonte).await {
            Ok(id) => id,
            Err(ErroPlayer::SpotifyFechado) => {
                abrir();
                self.esperar_dispositivo(fonte, espera).await?
            }
            Err(e) => return Err(e),
        };
        let caminho = format!("/me/player/play?device_id={id}");
        let corpo = json!({ "context_uri": contexto });
        match self
            .chamar(fonte, reqwest::Method::PUT, &caminho, Some(&corpo))
            .await
        {
            // Com device_id isto quase não acontece; se acontecer, transfere e repete
            Err(ErroPlayer::SemDispositivoAtivo) => {
                let transferir = json!({ "device_ids": [id], "play": false });
                self.chamar(fonte, reqwest::Method::PUT, "/me/player", Some(&transferir))
                    .await?;
                self.chamar(fonte, reqwest::Method::PUT, &caminho, Some(&corpo))
                    .await
                    .map(|_| ())
            }
            r => r.map(|_| ()),
        }
    }

    async fn esperar_dispositivo(
        &self,
        fonte: &dyn FonteToken,
        espera: Duration,
    ) -> Result<String, ErroPlayer> {
        let fim = Instant::now() + espera;
        loop {
            match self.dispositivo(fonte).await {
                Ok(id) => return Ok(id),
                Err(ErroPlayer::SpotifyFechado) if Instant::now() < fim => {
                    tokio::time::sleep(Duration::from_millis(1000)).await
                }
                Err(e) => return Err(e),
            }
        }
    }

    /// Pausar o que já está pausado dá 403 "Restriction violated": para nós, sucesso.
    pub async fn pausar(&self, fonte: &dyn FonteToken) -> Result<(), ErroPlayer> {
        match self
            .chamar(fonte, reqwest::Method::PUT, "/me/player/pause", None)
            .await
        {
            Ok(_) | Err(ErroPlayer::Recusado) | Err(ErroPlayer::SemDispositivoAtivo) => Ok(()),
            Err(e) => Err(e),
        }
    }

    /// Retoma de onde parou (no Spotify deste computador).
    pub async fn retomar(&self, fonte: &dyn FonteToken) -> Result<(), ErroPlayer> {
        let id = self.dispositivo(fonte).await?;
        match self
            .chamar(
                fonte,
                reqwest::Method::PUT,
                &format!("/me/player/play?device_id={id}"),
                None,
            )
            .await
        {
            Ok(_) | Err(ErroPlayer::Recusado) => Ok(()), // já tocando
            Err(e) => Err(e),
        }
    }

    /// A música atual (204 = nada tocando).
    pub async fn atual(&self, fonte: &dyn FonteToken) -> Result<Option<Tocando>, ErroPlayer> {
        // /me/player (estado da reprodução) e não /me/player/currently-playing: traz
        // também o dispositivo (volume) numa chamada só
        let (status, corpo) = self
            .chamar(fonte, reqwest::Method::GET, "/me/player", None)
            .await?;
        if status == 204 || corpo.trim().is_empty() {
            return Ok(None);
        }
        let v: Value = serde_json::from_str(&corpo).unwrap_or(Value::Null);
        let item = &v["item"];
        if item.is_null() {
            return Ok(None);
        }
        let artistas = item["artists"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|x| x["name"].as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        Ok(Some(Tocando {
            tocando: v["is_playing"].as_bool().unwrap_or(false),
            musica: item["name"].as_str().unwrap_or("").to_owned(),
            artistas,
            capa: item["album"]["images"][0]["url"]
                .as_str()
                .map(str::to_owned),
            progresso_ms: v["progress_ms"].as_u64().unwrap_or(0),
            duracao_ms: item["duration_ms"].as_u64().unwrap_or(0),
            volume: if v["device"]["supports_volume"].as_bool().unwrap_or(true) {
                v["device"]["volume_percent"]
                    .as_u64()
                    .map(|x| x.min(100) as u8)
            } else {
                None
            },
        }))
    }

    /// Próxima música (POST /me/player/next).
    pub async fn proxima(&self, fonte: &dyn FonteToken) -> Result<(), ErroPlayer> {
        self.chamar(fonte, reqwest::Method::POST, "/me/player/next", None)
            .await
            .map(|_| ())
    }

    /// Música anterior (POST /me/player/previous).
    pub async fn anterior(&self, fonte: &dyn FonteToken) -> Result<(), ErroPlayer> {
        self.chamar(fonte, reqwest::Method::POST, "/me/player/previous", None)
            .await
            .map(|_| ())
    }

    /// Volume de 0 a 100 no dispositivo que está tocando.
    pub async fn volume(&self, fonte: &dyn FonteToken, percentual: u8) -> Result<(), ErroPlayer> {
        let caminho = format!("/me/player/volume?volume_percent={}", percentual.min(100));
        self.chamar(fonte, reqwest::Method::PUT, &caminho, None)
            .await
            .map(|_| ())
    }
}

// ------------------------------------------------------------------ comandos

fn abrir_spotify(app: &AppHandle) -> impl Fn() + Sync + '_ {
    // "spotify:" é o esquema que o app do Spotify registra no Mac e no Windows
    move || {
        let _ = app.opener().open_url("spotify:", None::<&str>);
    }
}

#[tauri::command]
pub async fn spotify_tocar(
    contexto: String,
    app: AppHandle,
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    let abrir = abrir_spotify(&app);
    player
        .tocar(&FonteBff(&estado), &contexto, &abrir, ESPERA_ABRIR)
        .await
}

#[tauri::command]
pub async fn spotify_pausar(
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    player.pausar(&FonteBff(&estado)).await
}

#[tauri::command]
pub async fn spotify_retomar(
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    player.retomar(&FonteBff(&estado)).await
}

#[tauri::command]
pub async fn spotify_proxima(
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    player.proxima(&FonteBff(&estado)).await
}

#[tauri::command]
pub async fn spotify_anterior(
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    player.anterior(&FonteBff(&estado)).await
}

#[tauri::command]
pub async fn spotify_volume(
    percentual: u8,
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<(), ErroPlayer> {
    player.volume(&FonteBff(&estado), percentual).await
}

#[tauri::command]
pub async fn spotify_atual(
    player: State<'_, Player>,
    estado: State<'_, Estado>,
) -> Result<Option<Tocando>, ErroPlayer> {
    player.atual(&FonteBff(&estado)).await
}

// ------------------------------------------------------------------ conectar

#[derive(Deserialize)]
struct Config {
    client_id: String,
    escopos: String,
}

/// Conecta a conta: abre o navegador na autorização do Spotify e espera o retorno em
/// http://127.0.0.1:43821/callback (este app, escutando SÓ no próprio computador).
#[tauri::command]
pub async fn spotify_conectar(app: AppHandle, estado: State<'_, Estado>) -> Result<(), String> {
    let r = ponte::chamar(&estado, "GET", "spotify/config", None, vec![]).await?;
    if r.status() != 200 {
        return Err(r.detalhe());
    }
    let config: Config = serde_json::from_str(r.corpo()).map_err(|e| e.to_string())?;

    let mut bytes = [0u8; 64];
    let mut bytes_state = [0u8; 16];
    getrandom::fill(&mut bytes).map_err(|e| e.to_string())?;
    getrandom::fill(&mut bytes_state).map_err(|e| e.to_string())?;
    let verifier = regras::verifier(&bytes);
    let state = regras::state(&bytes_state);

    // 127.0.0.1, nunca 0.0.0.0: outro computador da rede não alcança este retorno
    let ouvinte = TcpListener::bind(("127.0.0.1", PORTA_RETORNO)).map_err(|_| {
        format!("a porta {PORTA_RETORNO} está ocupada; feche o outro programa e tente de novo")
    })?;
    let url = regras::url_autorizacao(
        &config.client_id,
        &config.escopos,
        &regras::challenge(&verifier),
        &state,
    );
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| e.to_string())?;

    let code = tauri::async_runtime::spawn_blocking(move || {
        esperar_retorno(ouvinte, &state, Duration::from_secs(180))
    })
    .await
    .map_err(|e| e.to_string())??;

    let corpo =
        json!({ "code": code, "code_verifier": verifier, "redirect_uri": regras::redirect_uri() });
    let r = ponte::chamar(
        &estado,
        "POST",
        "spotify/conectar",
        Some("application/json".into()),
        corpo.to_string().into_bytes(),
    )
    .await?;
    if r.status() == 200 {
        Ok(())
    } else {
        Err(r.detalhe())
    }
}

fn pagina(titulo: &str, texto: &str) -> String {
    let html = format!(
        "<!doctype html><meta charset=utf-8><title>estuda-ai</title>\
         <body style=\"font-family:Georgia,serif;background:#faf8f4;color:#1c1c1a;padding:4rem;max-width:32rem;margin:auto\">\
         <h1 style=\"font-weight:600\">{titulo}</h1><p>{texto}</p></body>"
    );
    format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{html}",
        html.len()
    )
}

/// Espera o navegador voltar com o `code` (até `prazo`). Pedidos que não são o
/// retorno (o navegador pede /favicon.ico) são respondidos e ignorados.
fn esperar_retorno(ouvinte: TcpListener, state: &str, prazo: Duration) -> Result<String, String> {
    let fim = Instant::now() + prazo;
    ouvinte.set_nonblocking(true).map_err(|e| e.to_string())?;
    loop {
        if Instant::now() > fim {
            return Err("o tempo para autorizar no Spotify acabou; tente de novo".into());
        }
        let (mut conexao, _) = match ouvinte.accept() {
            Ok(c) => c,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(100));
                continue;
            }
            Err(e) => return Err(e.to_string()),
        };
        let _ = conexao.set_nonblocking(false);
        let _ = conexao.set_read_timeout(Some(Duration::from_secs(5)));
        let mut linha = String::new();
        let _ = BufReader::new(&conexao).read_line(&mut linha);
        let (resultado, resposta) = match regras::ler_retorno(&linha, state) {
            Ok(code) => (
                Some(Ok(code)),
                pagina(
                    "Spotify conectado",
                    "Pode fechar esta aba e voltar ao estuda-ai.",
                ),
            ),
            Err(ErroRetorno::Negado) => (
                Some(Err("você não autorizou o acesso no Spotify".to_owned())),
                pagina(
                    "Conexão cancelada",
                    "Nada foi conectado. Você pode tentar de novo pelo app.",
                ),
            ),
            Err(ErroRetorno::StateDiferente) => (
                Some(Err(
                    "o retorno do Spotify não corresponde a este pedido; tente de novo".to_owned(),
                )),
                pagina(
                    "Pedido inválido",
                    "Este retorno não foi iniciado pelo estuda-ai.",
                ),
            ),
            Err(ErroRetorno::NaoERetorno) => (
                None,
                "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    .to_owned(),
            ),
        };
        let _ = conexao.write_all(resposta.as_bytes());
        if let Some(r) = resultado {
            return r;
        }
    }
}

#[cfg(test)]
mod testes {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Fonte de token dos testes: conta quantas vezes o token foi pedido "à força".
    struct FonteTeste {
        tokens: Vec<&'static str>,
        pedidos: AtomicUsize,
    }

    impl FonteToken for FonteTeste {
        fn token(&self, _forcar: bool) -> Futuro<'_, Result<String, ErroPlayer>> {
            let n = self.pedidos.fetch_add(1, Ordering::SeqCst);
            let t = self.tokens[n.min(self.tokens.len() - 1)].to_owned();
            Box::pin(async move { Ok(t) })
        }
    }

    fn fonte(tokens: &[&'static str]) -> FonteTeste {
        FonteTeste {
            tokens: tokens.to_vec(),
            pedidos: AtomicUsize::new(0),
        }
    }

    const DISPOSITIVOS: &str = r#"{"devices":[
        {"id":"cel","name":"iPhone","type":"Smartphone","is_active":true},
        {"id":"mac","name":"MacBook","type":"Computer","is_active":false}]}"#;

    #[tokio::test]
    async fn toca_no_computador_e_nao_no_celular() {
        let mut s = mockito::Server::new_async().await;
        s.mock("GET", "/me/player/devices")
            .with_body(DISPOSITIVOS)
            .create_async()
            .await;
        let play = s
            .mock("PUT", "/me/player/play?device_id=mac")
            .match_body(mockito::Matcher::Json(
                json!({"context_uri": "spotify:playlist:abc"}),
            ))
            .with_status(204)
            .create_async()
            .await;
        let player = Player::com_base(&s.url(), "MacBook");
        player
            .tocar(
                &fonte(&["t1"]),
                "spotify:playlist:abc",
                &|| {},
                Duration::ZERO,
            )
            .await
            .unwrap();
        play.assert_async().await;
    }

    #[tokio::test]
    async fn token_vencido_renova_uma_vez_e_repete() {
        let mut s = mockito::Server::new_async().await;
        s.mock("PUT", "/me/player/pause")
            .match_header("authorization", "Bearer velho")
            .with_status(401)
            .with_body(r#"{"error":{"status":401,"message":"The access token expired"}}"#)
            .create_async()
            .await;
        let ok = s
            .mock("PUT", "/me/player/pause")
            .match_header("authorization", "Bearer novo")
            .with_status(204)
            .create_async()
            .await;
        let f = fonte(&["velho", "novo"]);
        Player::com_base(&s.url(), "Mac").pausar(&f).await.unwrap();
        ok.assert_async().await;
        assert_eq!(f.pedidos.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn token_recusado_duas_vezes_nao_vira_laco() {
        let mut s = mockito::Server::new_async().await;
        let m = s
            .mock("GET", "/me/player")
            .with_status(401)
            .expect(2)
            .create_async()
            .await;
        let r = Player::com_base(&s.url(), "Mac")
            .atual(&fonte(&["a", "b", "c"]))
            .await;
        assert_eq!(r, Err(ErroPlayer::TokenExpirado));
        m.assert_async().await; // tentou exatamente 2 vezes
    }

    #[tokio::test]
    async fn spotify_fechado_abre_o_app_e_espera() {
        let mut s = mockito::Server::new_async().await;
        // 1a consulta: nada aberto; depois: o computador aparece
        s.mock("GET", "/me/player/devices")
            .with_body(r#"{"devices":[]}"#)
            .expect(1)
            .create_async()
            .await;
        let abriu = AtomicUsize::new(0);
        let player = Player::com_base(&s.url(), "Mac");
        let f = fonte(&["t"]);
        let abrir = || {
            abriu.fetch_add(1, Ordering::SeqCst);
        };
        // Sem dispositivo nunca: depois da espera, SpotifyFechado
        let r = player
            .tocar(&f, "spotify:playlist:abc", &abrir, Duration::ZERO)
            .await;
        assert_eq!(r, Err(ErroPlayer::SpotifyFechado));
        assert_eq!(abriu.load(Ordering::SeqCst), 1);

        s.reset();
        s.mock("GET", "/me/player/devices")
            .with_body(DISPOSITIVOS)
            .create_async()
            .await;
        s.mock("PUT", "/me/player/play?device_id=mac")
            .with_status(204)
            .create_async()
            .await;
        player
            .tocar(&f, "spotify:playlist:abc", &abrir, Duration::ZERO)
            .await
            .unwrap();
        assert_eq!(abriu.load(Ordering::SeqCst), 1); // já estava aberto: não abriu de novo
    }

    #[tokio::test]
    async fn limite_de_taxa_segura_as_proximas_chamadas() {
        let mut s = mockito::Server::new_async().await;
        let m = s
            .mock("GET", "/me/player")
            .with_status(429)
            .with_header("retry-after", "30")
            .expect(1)
            .create_async()
            .await;
        let player = Player::com_base(&s.url(), "Mac");
        let f = fonte(&["t"]);
        assert_eq!(
            player.atual(&f).await,
            Err(ErroPlayer::Limite {
                espera_s: 30,
                cota: false
            })
        );
        // A 2a chamada nem sai do computador (o mock aceita só 1)
        assert!(matches!(
            player.atual(&f).await,
            Err(ErroPlayer::Limite { .. })
        ));
        m.assert_async().await;
    }

    #[tokio::test]
    async fn pausar_o_que_ja_esta_pausado_e_sucesso() {
        let mut s = mockito::Server::new_async().await;
        s.mock("PUT", "/me/player/pause")
            .with_status(403)
            .with_body(r#"{"error":{"status":403,"message":"Player command failed: Restriction violated","reason":"UNKNOWN"}}"#)
            .create_async()
            .await;
        assert_eq!(
            Player::com_base(&s.url(), "Mac")
                .pausar(&fonte(&["t"]))
                .await,
            Ok(())
        );
    }

    #[tokio::test]
    async fn sem_premium_e_erro_proprio() {
        let mut s = mockito::Server::new_async().await;
        s.mock("GET", "/me/player/devices")
            .with_body(DISPOSITIVOS)
            .create_async()
            .await;
        s.mock("PUT", "/me/player/play?device_id=mac")
            .with_status(403)
            .with_body(r#"{"error":{"status":403,"message":"Premium required","reason":"PREMIUM_REQUIRED"}}"#)
            .create_async()
            .await;
        let r = Player::com_base(&s.url(), "MacBook")
            .tocar(&fonte(&["t"]), "spotify:playlist:a", &|| {}, Duration::ZERO)
            .await;
        assert_eq!(r, Err(ErroPlayer::SemPremium));
    }

    #[tokio::test]
    async fn musica_atual() {
        let mut s = mockito::Server::new_async().await;
        s.mock("GET", "/me/player")
            .with_body(
                r#"{"is_playing":true,"progress_ms":61000,"device":{"volume_percent":65,"supports_volume":true},"item":{"name":"Clair de Lune",
                "duration_ms":300000,"artists":[{"name":"Debussy"},{"name":"Orq."}],
                "album":{"images":[{"url":"https://i.scdn.co/capa"}]}}}"#,
            )
            .create_async()
            .await;
        let t = Player::com_base(&s.url(), "Mac")
            .atual(&fonte(&["t"]))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            (t.musica.as_str(), t.artistas.as_str(), t.tocando),
            ("Clair de Lune", "Debussy, Orq.", true)
        );
        assert_eq!(t.capa.as_deref(), Some("https://i.scdn.co/capa"));
        assert_eq!(t.volume, Some(65));
    }

    #[tokio::test]
    async fn proxima_anterior_e_volume() {
        let mut s = mockito::Server::new_async().await;
        let prox = s
            .mock("POST", "/me/player/next")
            .with_status(204)
            .create_async()
            .await;
        let ant = s
            .mock("POST", "/me/player/previous")
            .with_status(204)
            .create_async()
            .await;
        // Acima de 100 é limitado a 100 (o Spotify recusaria)
        let vol = s
            .mock("PUT", "/me/player/volume?volume_percent=100")
            .with_status(204)
            .create_async()
            .await;
        let player = Player::com_base(&s.url(), "Mac");
        let f = fonte(&["t"]);
        player.proxima(&f).await.unwrap();
        player.anterior(&f).await.unwrap();
        player.volume(&f, 150).await.unwrap();
        prox.assert_async().await;
        ant.assert_async().await;
        vol.assert_async().await;
    }

    #[tokio::test]
    async fn dispositivo_sem_controle_de_volume() {
        let mut s = mockito::Server::new_async().await;
        s.mock("GET", "/me/player")
            .with_body(r#"{"is_playing":false,"device":{"volume_percent":40,"supports_volume":false},"item":{"name":"x","artists":[]}}"#)
            .create_async()
            .await;
        let t = Player::com_base(&s.url(), "Mac")
            .atual(&fonte(&["t"]))
            .await
            .unwrap()
            .unwrap();
        assert_eq!((t.tocando, t.volume), (false, None));
    }

    #[tokio::test]
    async fn nada_tocando() {
        let mut s = mockito::Server::new_async().await;
        s.mock("GET", "/me/player")
            .with_status(204)
            .create_async()
            .await;
        assert_eq!(
            Player::com_base(&s.url(), "Mac")
                .atual(&fonte(&["t"]))
                .await,
            Ok(None)
        );
    }

    #[test]
    fn retorno_do_navegador_pelo_socket() {
        let ouvinte = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let porta = ouvinte.local_addr().unwrap().port();
        let navegador = std::thread::spawn(move || {
            let mut favicon = std::net::TcpStream::connect(("127.0.0.1", porta)).unwrap();
            favicon
                .write_all(b"GET /favicon.ico HTTP/1.1\r\n\r\n")
                .unwrap();
            let mut c = std::net::TcpStream::connect(("127.0.0.1", porta)).unwrap();
            c.write_all(b"GET /callback?code=CODIGO&state=ST HTTP/1.1\r\nHost: x\r\n\r\n")
                .unwrap();
            let mut resposta = String::new();
            let _ = std::io::Read::read_to_string(&mut c, &mut resposta);
            resposta
        });
        assert_eq!(
            esperar_retorno(ouvinte, "ST", Duration::from_secs(5)),
            Ok("CODIGO".into())
        );
        assert!(navegador.join().unwrap().contains("Spotify conectado"));
    }
}
