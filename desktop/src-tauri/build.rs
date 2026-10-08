/// Os comandos do app (src/ponte.rs) declarados num "manifesto": sem isso, qualquer
/// janela poderia chamar qualquer comando. Com ele, cada comando vira uma permissão
/// (allow-chamar-api...) que precisa ser concedida em capabilities/*.json, como as dos
/// plugins. É o princípio do menor privilégio do banco (papel estuda_ai_app), aplicado
/// ao IPC: a página só alcança o que foi listado.
const COMANDOS: &[&str] = &["chamar_api", "entrar", "sair"];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMANDOS)),
    )
    .expect("falha no build do Tauri");
}
