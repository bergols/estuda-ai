//! App desktop do estuda-ai: as telas são o frontend Next.js exportado como arquivos
//! estáticos (frontend/out); este lado cuida do que uma página não pode fazer sozinha.

mod cofre;
mod ponte;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let estado = ponte::Estado::novo().expect("configuração do servidor inválida");
    tauri::Builder::default()
        .manage(estado)
        .invoke_handler(tauri::generate_handler![
            ponte::chamar_api,
            ponte::entrar,
            ponte::sair
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o estuda-ai");
}
