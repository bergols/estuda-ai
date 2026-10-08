//! A janela no modo foco: tela cheia, sempre por cima das outras.
//!
//! No macOS, a tela cheia NATIVA (botão verde) cria um "Space" separado: ao trocar de
//! app (Cmd+Tab) o sistema simplesmente vai para outro Space, e o "sempre por cima" não
//! adianta nada. A tela cheia SIMPLES (como era antes do OS X Lion) cobre a tela no
//! Space atual, e com always_on_top a janela fica por cima dos outros apps; visível em
//! todos os Spaces, ela acompanha se você trocar de mesa. No Windows, o Tauri usa a
//! tela cheia comum, que já fica no mesmo "espaço".

use tauri::WebviewWindow;

#[tauri::command]
pub fn modo_foco(ativar: bool, janela: WebviewWindow) -> Result<(), String> {
    let erro = |e: tauri::Error| e.to_string();
    janela.set_always_on_top(ativar).map_err(erro)?;
    #[cfg(target_os = "macos")]
    janela.set_visible_on_all_workspaces(ativar).map_err(erro)?;
    janela.set_simple_fullscreen(ativar).map_err(erro)?;
    if ativar {
        janela.set_focus().map_err(erro)?;
    }
    Ok(())
}

/// "macos", "windows" ou "linux" (o campo `sistema` da sessão).
#[tauri::command]
pub fn sistema() -> &'static str {
    match std::env::consts::OS {
        "macos" => "macos",
        "windows" => "windows",
        _ => "linux",
    }
}
