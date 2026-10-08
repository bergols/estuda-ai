//! O token de sessão guardado no cofre do sistema operacional.
//!
//! Na web, o JWT fica num cookie httpOnly que o JavaScript não lê. No desktop não há
//! cookie de servidor: o equivalente é deixar o token só do lado Rust, no cofre do
//! sistema (Keychain no macOS, Gerenciador de Credenciais no Windows), cifrado pelo
//! próprio sistema e atrelado ao seu usuário. A página nunca recebe o token: ela pede
//! "chame tal rota" e o Rust acrescenta o "Authorization: Bearer".
//!
//! Por que não um arquivo de configuração? Um arquivo em texto puro é lido por qualquer
//! programa rodando como você e vai parar em backups/sincronizações de pasta. O cofre
//! exige a sua sessão do sistema e, no macOS, pede permissão quando outro app tenta ler.
//!
//! Uma entrada por servidor (a "conta" é a origem da API): o token do servidor de
//! desenvolvimento (localhost) nunca é mandado para o de produção, e vice-versa.

use keyring::Entry;

const SERVICO: &str = "estuda-ai";

fn entrada(origem: &str) -> Result<Entry, String> {
    Entry::new(SERVICO, origem).map_err(|e| format!("cofre do sistema indisponível: {e}"))
}

pub fn ler(origem: &str) -> Result<Option<String>, String> {
    match entrada(origem)?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("não consegui ler o cofre do sistema: {e}")),
    }
}

pub fn gravar(origem: &str, token: &str) -> Result<(), String> {
    entrada(origem)?
        .set_password(token)
        .map_err(|e| format!("não consegui gravar no cofre do sistema: {e}"))
}

pub fn apagar(origem: &str) -> Result<(), String> {
    match entrada(origem)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("não consegui apagar do cofre do sistema: {e}")),
    }
}
