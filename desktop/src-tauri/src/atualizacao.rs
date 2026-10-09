//! Atualização automática (tauri-plugin-updater).
//!
//! O app lê o `latest.json` da última Release publicada do GitHub (endereço em
//! tauri.conf.json, plugins.updater), compara com a própria versão e, se houver uma
//! mais nova, baixa o pacote e confere a ASSINATURA antes de instalar. A assinatura é
//! o que impede alguém de trocar o instalador no caminho (ou na Release) por outro:
//! só a chave privada, guardada nos secrets do GitHub e no seu computador, assina, e a
//! chave pública está embutida no app. HTTPS sozinho não basta: ele garante com quem
//! você fala, não que o arquivo é o que o CI gerou.
//!
//! A página decide QUANDO instalar (lib/foco/atualizacao.ts): ao abrir o app, sem
//! sessão em andamento, instala sozinha; depois disso, só avisa, para não reiniciar no
//! meio do que você estiver fazendo.

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, State};
use tauri_plugin_updater::{Update, UpdaterExt};

/// A atualização encontrada pela última verificação (instalar usa exatamente ela).
#[derive(Default)]
pub struct Pendente(Mutex<Option<Update>>);

#[derive(Serialize)]
pub struct Novidade {
    versao: String,
    notas: Option<String>,
}

/// Há versão nova? `None` também em build de desenvolvimento: o `tauri dev` não pode
/// se substituir pelo instalador da Release.
#[tauri::command]
pub async fn atualizacao_verificar(
    app: AppHandle,
    pendente: State<'_, Pendente>,
) -> Result<Option<Novidade>, String> {
    if cfg!(debug_assertions) {
        return Ok(None);
    }
    let atualizacao = app
        .updater_builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?;
    let novidade = atualizacao.as_ref().map(|u| Novidade {
        versao: u.version.clone(),
        notas: u.body.clone(),
    });
    *pendente.0.lock().map_err(|e| e.to_string())? = atualizacao;
    Ok(novidade)
}

/// Baixa, confere a assinatura, instala e reinicia. No Windows o instalador (NSIS,
/// modo "passive": só uma barra de progresso) fecha o app sozinho e o abre de novo.
#[tauri::command]
pub async fn atualizacao_instalar(
    app: AppHandle,
    pendente: State<'_, Pendente>,
) -> Result<(), String> {
    let atualizacao = pendente
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .take()
        .ok_or("nenhuma atualização verificada")?;
    atualizacao
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart()
}
