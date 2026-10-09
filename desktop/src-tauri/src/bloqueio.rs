//! Bloqueios do modo foco: sites (o guardião, com a senha do computador) e programas
//! (o monitor de processos, sem senha). As regras e os scripts estão em
//! `nucleo::bloqueio`; aqui só a execução. Explicação em docs/modo-foco.md.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use nucleo::bloqueio::{self as regras, Guardiao, MARCA_INICIO, Sistema};
use serde::Serialize;
use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System, UpdateKind};
use tauri::{AppHandle, Emitter, Manager, State};

/// Prazo máximo do bloqueio de sites, mesmo se a sessão pedir mais.
const PRAZO_MAXIMO_MIN: u64 = 12 * 60;
/// De quanto em quanto tempo o monitor procura programas bloqueados.
const INTERVALO_MONITOR: Duration = Duration::from_secs(2);

const MOTIVO: &str = "O estuda-ai vai bloquear os sites da sua lista durante a sessão de \
estudo (arquivo hosts). O bloqueio sai sozinho no fim da sessão, se o app fechar ou se o \
computador reiniciar.";

// ------------------------------------------------------------------ caminhos

fn caminho_hosts() -> PathBuf {
    if cfg!(windows) {
        let raiz = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned());
        PathBuf::from(raiz).join(r"System32\drivers\etc\hosts")
    } else {
        PathBuf::from("/etc/hosts")
    }
}

/// Pasta do sistema onde o guardião guarda as cópias (só o administrador altera).
fn pasta_estado() -> PathBuf {
    if cfg!(windows) {
        let raiz = std::env::var("ProgramData").unwrap_or_else(|_| r"C:\ProgramData".to_owned());
        PathBuf::from(raiz).join("estuda-ai")
    } else {
        PathBuf::from("/Library/Application Support/estuda-ai")
    }
}

fn arquivo_sinal(app: &AppHandle) -> Result<PathBuf, String> {
    let pasta = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&pasta).map_err(|e| e.to_string())?;
    Ok(pasta.join("foco-liberar"))
}

fn agora_s() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

fn bloqueado_agora() -> bool {
    std::fs::read_to_string(caminho_hosts()).is_ok_and(|t| t.contains(MARCA_INICIO))
}

fn guardiao(app: &AppHandle, dominios: Vec<String>, minutos: u64) -> Result<Guardiao, String> {
    let mut token = [0u8; 16];
    getrandom::fill(&mut token).map_err(|e| e.to_string())?;
    Ok(Guardiao {
        hosts: caminho_hosts().display().to_string(),
        estado: pasta_estado().display().to_string(),
        sinal: arquivo_sinal(app)?.display().to_string(),
        pid: std::process::id(),
        nome_processo: "estuda-ai".into(),
        ate: agora_s() + minutos.clamp(1, PRAZO_MAXIMO_MIN) * 60,
        token: token.iter().map(|b| format!("{b:02x}")).collect(),
        dominios,
        teste: false,
    })
}

// ------------------------------------------------------- rodar como administrador

/// Mac: o AppleScript abre a janela de senha do próprio sistema. Volta quando o
/// "aplicar" termina (o vigia segue em segundo plano).
#[cfg(target_os = "macos")]
fn como_administrador(g: &Guardiao, so_restaurar: bool) -> Result<(), String> {
    let comando = if so_restaurar {
        g.comando_mac_restaurar()
    } else {
        g.comando_mac()
    };
    let fonte = format!(
        "do shell script {} with prompt {} with administrator privileges",
        regras::aspas_applescript(&comando),
        regras::aspas_applescript(MOTIVO)
    );
    let saida = Command::new("osascript")
        .arg("-e")
        .arg(fonte)
        .output()
        .map_err(|e| e.to_string())?;
    if saida.status.success() {
        return Ok(());
    }
    let erro = String::from_utf8_lossy(&saida.stderr);
    // -128 = "User canceled" (fechou a janela de senha)
    Err(if erro.contains("-128") {
        "cancelado".to_owned()
    } else {
        erro.trim().to_owned()
    })
}

/// Windows: o UAC pede a confirmação; o PowerShell elevado roda o guardião numa
/// janela escondida. `Start-Process -Verb RunAs` volta assim que o UAC é aceito (ou
/// dá erro se você recusar); quem confirma o bloqueio é a leitura do hosts depois.
#[cfg(windows)]
fn como_administrador(g: &Guardiao, so_restaurar: bool) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const SEM_JANELA: u32 = 0x0800_0000; // CREATE_NO_WINDOW
    let comando = if so_restaurar {
        g.comando_windows_restaurar()
    } else {
        g.comando_windows()
    };
    let iniciar = format!(
        "Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden {} -ArgumentList \
         '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{}'",
        if so_restaurar { "-Wait" } else { "" },
        regras::comando_codificado_ps(&comando)
    );
    let saida = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &iniciar])
        .creation_flags(SEM_JANELA)
        .output()
        .map_err(|e| e.to_string())?;
    if saida.status.success() {
        Ok(())
    } else {
        let erro = String::from_utf8_lossy(&saida.stderr);
        Err(if erro.contains("cancel") {
            "cancelado".to_owned()
        } else {
            erro.trim().to_owned()
        })
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
fn como_administrador(_: &Guardiao, _: bool) -> Result<(), String> {
    Err("bloqueio de sites só no macOS e no Windows".into())
}

async fn esperar(condicao: impl Fn() -> bool, limite: Duration) -> bool {
    let fim = Instant::now() + limite;
    while Instant::now() < fim {
        if condicao() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    condicao()
}

// ------------------------------------------------------------------ comandos

/// Bloqueia os sites até o fim da sessão (`minutos` a partir de agora, no máximo 12 h).
/// Erro "cancelado" = a pessoa não deu a senha: a sessão segue sem bloqueio de sites.
#[tauri::command]
pub async fn foco_sites_bloquear(
    app: AppHandle,
    dominios: Vec<String>,
    minutos: u64,
) -> Result<(), String> {
    let dominios = regras::dominios_para_bloquear(&dominios);
    if dominios.is_empty() {
        return Ok(());
    }
    let g = guardiao(&app, dominios, minutos)?;
    // Um sinal velho liberaria o bloqueio novo na hora
    let _ = std::fs::remove_file(&g.sinal);
    tauri::async_runtime::spawn_blocking(move || como_administrador(&g, false))
        .await
        .map_err(|e| e.to_string())??;
    if esperar(bloqueado_agora, Duration::from_secs(30)).await {
        Ok(())
    } else {
        Err("o bloqueio não apareceu no arquivo hosts".into())
    }
}

/// Pede ao guardião para liberar. `false` = ele não respondeu em 10 s (morto por
/// alguém?): a tela oferece "desbloquear agora", que pede a senha.
#[tauri::command]
pub async fn foco_sites_liberar(app: AppHandle) -> Result<bool, String> {
    if !bloqueado_agora() {
        return Ok(true);
    }
    std::fs::write(arquivo_sinal(&app)?, b"").map_err(|e| e.to_string())?;
    Ok(esperar(|| !bloqueado_agora(), Duration::from_secs(10)).await)
}

/// Restauração "à força" (com a senha), para quando o guardião não está mais lá.
#[tauri::command]
pub async fn foco_sites_restaurar(app: AppHandle) -> Result<(), String> {
    let g = guardiao(&app, vec![], 1)?;
    tauri::async_runtime::spawn_blocking(move || como_administrador(&g, true))
        .await
        .map_err(|e| e.to_string())??;
    if esperar(|| !bloqueado_agora(), Duration::from_secs(30)).await {
        Ok(())
    } else {
        Err("o arquivo hosts continua com o bloqueio".into())
    }
}

#[derive(Serialize)]
pub struct SituacaoSites {
    bloqueado: bool,
    /// O guardião deu sinal de vida nos últimos 15 s
    guardiao_vivo: bool,
}

/// Ao abrir o app: ficou bloqueio sem guardião (sessão que travou feio)?
#[tauri::command]
pub fn foco_sites_situacao() -> SituacaoSites {
    let vivo = std::fs::read_to_string(pasta_estado().join("vivo"))
        .ok()
        .and_then(|t| t.trim().parse::<u64>().ok())
        .is_some_and(|t| agora_s().saturating_sub(t) < 15);
    SituacaoSites {
        bloqueado: bloqueado_agora(),
        guardiao_vivo: vivo,
    }
}

// ----------------------------------------------------------------- programas

/// O monitor em andamento (um por vez).
#[derive(Default)]
pub struct Monitor(Mutex<Option<tauri::async_runtime::JoinHandle<()>>>);

#[derive(Clone, Serialize)]
struct ProgramaFechado {
    programa: String,
}

fn sistema_atual() -> Sistema {
    if cfg!(windows) {
        Sistema::Windows
    } else {
        Sistema::Macos
    }
}

/// Fecha um processo: no Mac/Linux com SIGTERM (o app pode salvar e sair direito) e,
/// se não der, à força; no Windows, à força (não há "pedir para sair" sem janela).
fn fechar(processo: &sysinfo::Process) -> bool {
    processo.kill_with(sysinfo::Signal::Term).unwrap_or(false) || processo.kill()
}

/// Começa a fechar os programas da lista enquanto a sessão durar. Cada programa
/// fechado vira um evento "programa-fechado" (a tela registra na sessão), no máximo um
/// a cada 30 s por programa: um app que reabre sozinho não enche a sessão de eventos.
#[tauri::command]
pub fn foco_programas_iniciar(app: AppHandle, monitor: State<'_, Monitor>, lista: Vec<String>) {
    let lista: Vec<String> = lista.into_iter().filter(|p| !p.trim().is_empty()).collect();
    let mut atual = monitor.0.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(anterior) = atual.take() {
        anterior.abort();
    }
    if lista.is_empty() {
        return;
    }
    *atual = Some(tauri::async_runtime::spawn(async move {
        let sistema = sistema_atual();
        let eu = Pid::from_u32(std::process::id());
        let mut sys = System::new();
        let mut avisado: HashMap<String, Instant> = HashMap::new();
        loop {
            sys.refresh_processes_specifics(
                ProcessesToUpdate::All,
                true,
                ProcessRefreshKind::nothing().with_exe(UpdateKind::OnlyIfNotSet),
            );
            for (pid, processo) in sys.processes() {
                if *pid == eu || processo.parent() == Some(eu) {
                    continue;
                }
                let nome = processo.name().to_string_lossy();
                let exe = processo.exe().map(|p| p.display().to_string());
                let Some(entrada) =
                    regras::entrada_que_fecha(&nome, exe.as_deref(), &lista, sistema)
                else {
                    continue;
                };
                if fechar(processo) {
                    let recente = avisado
                        .get(entrada)
                        .is_some_and(|t| t.elapsed() < Duration::from_secs(30));
                    if !recente {
                        avisado.insert(entrada.to_owned(), Instant::now());
                        let _ = app.emit(
                            "programa-fechado",
                            ProgramaFechado {
                                programa: entrada.to_owned(),
                            },
                        );
                    }
                }
            }
            tokio::time::sleep(INTERVALO_MONITOR).await;
        }
    }));
}

#[tauri::command]
pub fn foco_programas_parar(monitor: State<'_, Monitor>) {
    if let Some(tarefa) = monitor.0.lock().unwrap_or_else(|e| e.into_inner()).take() {
        tarefa.abort();
    }
}
