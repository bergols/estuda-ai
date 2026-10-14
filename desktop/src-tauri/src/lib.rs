//! App desktop do estuda-ai: as telas são o frontend Next.js exportado como arquivos
//! estáticos (frontend/out); este lado cuida do que uma página não pode fazer sozinha.

mod atualizacao;
mod bloqueio;
mod cofre;
mod janela;
mod ponte;
mod servidor;
mod sincronia;
mod spotify;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let estado = ponte::Estado::novo().expect("configuração do servidor inválida");
    tauri::Builder::default()
        .manage(estado)
        .manage(spotify::Player::novo())
        .manage(atualizacao::Pendente::default())
        .manage(bloqueio::Monitor::default())
        .manage(servidor::Servidor::default())
        // Uma instância só: abrir de novo traz a janela que já existe para a frente, em
        // vez de duas instâncias ligando/desligando o mesmo servidor (e a mesma fila)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(janela) = app.get_webview_window("principal") {
                let _ = janela.show();
                let _ = janela.unminimize();
                let _ = janela.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // Fila local (SQLite na pasta de dados do app) e o laço de envio
            let sincronia = sincronia::Sincronia::abrir(app.handle())?;
            app.manage(sincronia);
            sincronia::iniciar(app.handle().clone());
            servidor::iniciar_backups(app.handle().clone());
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
            atualizacao::atualizacao_verificar,
            atualizacao::atualizacao_instalar,
            bloqueio::foco_sites_bloquear,
            bloqueio::foco_sites_liberar,
            bloqueio::foco_sites_restaurar,
            bloqueio::foco_sites_situacao,
            bloqueio::foco_programas_iniciar,
            bloqueio::foco_programas_parar,
            servidor::servidor_situacao,
            servidor::servidor_ligar,
        ])
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o estuda-ai")
        .run(|app, evento| match evento {
            // Reinício da atualização automática: marca para o servidor não desligar
            tauri::RunEvent::ExitRequested { code, .. } => servidor::ao_pedir_saida(app, code),
            // Fim normal (última janela fechada ou Cmd+Q, que no macOS só chega aqui):
            // desliga o servidor do Mac antes de sair. Travou ou foi morto à força: nem
            // passa por aqui, e o servidor fica ligado (decisão do autor; ver servidor.rs).
            tauri::RunEvent::Exit => servidor::ao_sair(app),
            _ => {}
        });
}
