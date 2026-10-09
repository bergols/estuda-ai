//! App desktop do estuda-ai: as telas são o frontend Next.js exportado como arquivos
//! estáticos (frontend/out); este lado cuida do que uma página não pode fazer sozinha.

mod cofre;
mod janela;
mod ponte;
mod sincronia;
mod spotify;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let estado = ponte::Estado::novo().expect("configuração do servidor inválida");
    tauri::Builder::default()
        .manage(estado)
        .manage(spotify::Player::novo())
        .plugin(tauri_plugin_opener::init())
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
            janela::modo_foco,
            janela::sistema,
            spotify::spotify_conectar,
            spotify::spotify_tocar,
            spotify::spotify_pausar,
            spotify::spotify_retomar,
            spotify::spotify_atual,
            spotify::spotify_proxima,
            spotify::spotify_anterior,
            spotify::spotify_volume,
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o estuda-ai");
}
