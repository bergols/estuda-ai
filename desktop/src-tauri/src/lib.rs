//! App desktop do estuda-ai: as telas são o frontend Next.js exportado como arquivos
//! estáticos (frontend/out); este lado cuida do que uma página não pode fazer sozinha.

mod cofre;
mod ponte;
mod sincronia;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let estado = ponte::Estado::novo().expect("configuração do servidor inválida");
    tauri::Builder::default()
        .manage(estado)
        .setup(|app| {
            // Fila local (SQLite na pasta de dados do app) e o laço de envio
            let sincronia = sincronia::Sincronia::abrir(app.handle())?;
            app.manage(sincronia);
            sincronia::iniciar(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ponte::chamar_api,
            ponte::entrar,
            ponte::sair,
            sincronia::salvar_sessao,
            sincronia::sessao_em_andamento,
            sincronia::situacao_sincronia,
            sincronia::sincronizar_agora,
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o estuda-ai");
}
