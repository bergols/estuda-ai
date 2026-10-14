//! O servidor local: provisoriamente, a API de produção roda no Mac do autor (Colima +
//! docker-compose.prod.yml em ~/estuda-ai-servidor). Para não gastar bateria o dia
//! todo, o app do Mac liga o servidor ao abrir e desliga ao fechar (decisão do autor;
//! detalhes em docs/deploy.md, seção 4c).
//!
//! - Só vale no macOS e só se ~/estuda-ai-servidor existir: em qualquer outro
//!   computador (o Windows, um Mac sem o servidor) nada disso roda.
//! - A página só pode pedir "como está?" e "ligar". Desligar acontece aqui, no fechamento
//!   normal do app. Nenhum argumento vem da página: os comandos e caminhos são fixos.
//! - Se o app travar ou for fechado à força, o servidor fica LIGADO (decisão do autor):
//!   melhor gastar bateria do que derrubar a API no meio de algo. Abrir o app de novo
//!   encontra tudo no ar e segue.
//! - Desligar é `docker compose stop` (o Postgres recebe SIGTERM e fecha limpo, gravando
//!   tudo no disco) e depois `colima stop` (desliga a VM e devolve RAM e CPU ao Mac). Os
//!   dados ficam no disco da VM, que sobrevive ao stop. Nunca `colima delete`.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime};

use nucleo::servidor::{INTERVALO_BACKUP, backup_vencido, ultimo_backup};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

/// Apps abertos pelo Finder não herdam o PATH do terminal: o Colima e o Docker do
/// Homebrew (e o limactl, que o Colima chama) precisam dele explícito.
const PATH: &str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
/// O Caddy do compose de produção, só no 127.0.0.1 (o /health não pede o segredo do BFF)
const SAUDE: &str = "http://127.0.0.1:8080/health";
const ESPERA_SAUDE: Duration = Duration::from_secs(180);

#[derive(Default)]
pub struct Servidor {
    /// Um ligar/desligar por vez (dois cliques, ou o fechamento no meio do ligar)
    ocupado: tokio::sync::Mutex<()>,
    /// O fechamento já está desligando: o segundo pedido de saída passa direto
    desligando: AtomicBool,
}

fn pasta() -> Option<PathBuf> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let p = PathBuf::from(std::env::var_os("HOME")?).join("estuda-ai-servidor");
    p.join("docker-compose.prod.yml").is_file().then_some(p)
}

fn binario(nome: &str) -> PathBuf {
    ["/opt/homebrew/bin", "/usr/local/bin"]
        .iter()
        .map(|d| Path::new(d).join(nome))
        .find(|p| p.is_file())
        .unwrap_or_else(|| PathBuf::from(nome))
}

/// Roda um comando fixo com prazo. Saída descartada (só o código importa), menos o
/// fim do stderr, que vira a mensagem de erro.
fn rodar(nome: &str, args: &[&str], dentro: Option<&Path>, prazo: Duration) -> Result<(), String> {
    let mut cmd = Command::new(binario(nome));
    cmd.args(args)
        .env("PATH", PATH)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    if let Some(d) = dentro {
        cmd.current_dir(d);
    }
    let mut filho = cmd.spawn().map_err(|e| format!("{nome}: {e}"))?;
    let fim = Instant::now() + prazo;
    loop {
        if let Some(status) = filho.try_wait().map_err(|e| e.to_string())? {
            if status.success() {
                return Ok(());
            }
            let mut erro = String::new();
            if let Some(mut s) = filho.stderr.take() {
                use std::io::Read;
                let _ = s.read_to_string(&mut erro);
            }
            let ultima = erro
                .lines()
                .rev()
                .find(|l| !l.trim().is_empty())
                .unwrap_or("");
            return Err(format!("{nome} {}: {}", args.join(" "), ultima.trim()));
        }
        if Instant::now() > fim {
            let _ = filho.kill();
            return Err(format!(
                "{nome} {}: passou de {} s",
                args.join(" "),
                prazo.as_secs()
            ));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn colima_ligado() -> bool {
    rodar("colima", &["status"], None, Duration::from_secs(20)).is_ok()
}

async fn saudavel() -> bool {
    let Ok(cliente) = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
    else {
        return false;
    };
    cliente
        .get(SAUDE)
        .send()
        .await
        .is_ok_and(|r| r.status().is_success())
}

fn compose(pasta: &Path, args: &[&str], prazo: Duration) -> Result<(), String> {
    let mut todos = vec!["compose", "-f", "docker-compose.prod.yml"];
    todos.extend_from_slice(args);
    rodar("docker", &todos, Some(pasta), prazo)
}

/// Roda deploy/backup.sh se o último backup tem 24 h ou mais (precisa do banco no ar).
fn backup_se_preciso(pasta: &Path) -> Result<bool, String> {
    let destino = PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join("backups");
    let arquivos: Vec<(String, SystemTime)> = std::fs::read_dir(&destino)
        .map(|d| {
            d.filter_map(Result::ok)
                .filter_map(|e| {
                    let quando = e.metadata().ok()?.modified().ok()?;
                    Some((e.file_name().to_string_lossy().into_owned(), quando))
                })
                .collect()
        })
        .unwrap_or_default();
    let ultimo = ultimo_backup(arquivos.iter().map(|(n, t)| (n.as_str(), *t)));
    if !backup_vencido(ultimo, SystemTime::now(), INTERVALO_BACKUP) {
        return Ok(false);
    }
    rodar(
        "bash",
        &["deploy/backup.sh"],
        Some(pasta),
        Duration::from_secs(600),
    )
    .map(|_| true)
}

#[derive(Serialize)]
pub struct Situacao {
    /// Este computador hospeda o servidor (Mac com ~/estuda-ai-servidor)
    gerenciado: bool,
    /// A API respondeu o /health agora
    saudavel: bool,
}

#[tauri::command]
pub async fn servidor_situacao() -> Situacao {
    let gerenciado = pasta().is_some();
    Situacao {
        gerenciado,
        saudavel: gerenciado && saudavel().await,
    }
}

/// Liga o que estiver desligado e espera a API responder. As etapas vão para a tela
/// pelo evento "servidor-etapa" ("ligando a máquina virtual"...).
#[tauri::command]
pub async fn servidor_ligar(app: AppHandle) -> Result<(), String> {
    let Some(pasta) = pasta() else {
        return Ok(());
    };
    let servidor = app.state::<Servidor>();
    let _vez = servidor.ocupado.lock().await;
    if saudavel().await {
        return Ok(());
    }
    let etapa = |texto: &str| {
        let _ = app.emit("servidor-etapa", texto.to_owned());
    };
    let p = pasta.clone();
    etapa("ligando a máquina virtual (Colima)");
    tauri::async_runtime::spawn_blocking(move || {
        if !colima_ligado() {
            rodar("colima", &["start"], None, Duration::from_secs(240))?;
        }
        Ok::<_, String>(())
    })
    .await
    .map_err(|e| e.to_string())??;
    etapa("subindo o banco e a API");
    // up -d sem --build: as imagens já existem (o atualizar.sh é quem reconstrói)
    tauri::async_runtime::spawn_blocking(move || {
        compose(&p, &["up", "-d"], Duration::from_secs(240))
    })
    .await
    .map_err(|e| e.to_string())??;
    etapa("esperando a API responder");
    let fim = Instant::now() + ESPERA_SAUDE;
    while !saudavel().await {
        if Instant::now() > fim {
            return Err("a API não respondeu em 3 minutos".into());
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    // Backup do dia, se faltar (em segundo plano: a tela já pode seguir)
    tauri::async_runtime::spawn_blocking(move || {
        if let Err(e) = backup_se_preciso(&pasta) {
            eprintln!("backup: {e}");
        }
    });
    Ok(())
}

/// Enquanto o app fica aberto, um backup a cada 24 h (confere de hora em hora).
pub fn iniciar_backups(app: AppHandle) {
    let Some(pasta) = pasta() else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(60 * 60)).await;
            let servidor = app.state::<Servidor>();
            let Ok(_vez) = servidor.ocupado.try_lock() else {
                continue; // ligando ou desligando agora: na próxima hora
            };
            if saudavel().await {
                let p = pasta.clone();
                let _ = tauri::async_runtime::spawn_blocking(move || backup_se_preciso(&p)).await;
            }
        }
    });
}

/// Chamado no fechamento normal do app. `true` = o app deve esperar (o desligamento
/// começou e chama `app.exit` no fim); `false` = pode sair já (não há servidor, ou o
/// desligamento já terminou e este é o segundo pedido de saída).
pub fn ao_fechar(app: &AppHandle) -> bool {
    let Some(pasta) = pasta() else {
        return false;
    };
    let servidor = app.state::<Servidor>();
    if servidor.desligando.swap(true, Ordering::SeqCst) {
        return false;
    }
    // A janela some na hora; o processo termina o desligamento sozinho (~20 s)
    for janela in app.webview_windows().values() {
        let _ = janela.hide();
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let servidor = app.state::<Servidor>();
        let _vez = servidor.ocupado.lock().await;
        // 1. Sessões de estudo pendentes: sobem antes, enquanto a API está no ar
        let _ = tokio::time::timeout(Duration::from_secs(15), crate::sincronia::sincronizar(&app))
            .await;
        // 2. Backup (se vencido), compose stop, colima stop
        let resultado = tauri::async_runtime::spawn_blocking(move || {
            if colima_ligado() {
                if let Err(e) = backup_se_preciso(&pasta) {
                    eprintln!("backup: {e}");
                }
                compose(&pasta, &["stop"], Duration::from_secs(120))?;
                rodar("colima", &["stop"], None, Duration::from_secs(120))?;
            }
            Ok::<_, String>(())
        })
        .await;
        if let Ok(Err(e)) | Err(e) = resultado.map_err(|e| e.to_string()) {
            eprintln!("desligar o servidor: {e}");
        }
        app.exit(0);
    });
    true
}
