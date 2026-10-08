// Sem janela de terminal extra no Windows (em build de release)
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    estuda_ai_lib::run()
}
