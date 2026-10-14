//! Regras do servidor local (o Mac que hospeda a API, provisoriamente, até a VM Oracle):
//! o app desktop do Mac liga o Colima e o compose de produção ao abrir e desliga ao
//! fechar. Aqui só o que dá para testar sem Docker; a execução fica em
//! src-tauri/src/servidor.rs. Explicação em docs/deploy.md, seção 4c.

use std::time::{Duration, SystemTime};

/// Intervalo entre backups automáticos (no ligar, no desligar e enquanto estiver ligado).
pub const INTERVALO_BACKUP: Duration = Duration::from_secs(24 * 60 * 60);

/// O backup mais recente entre os arquivos de ~/backups: só os dumps completos do banco
/// (`banco_<quando>.dump`). Um `.parcial` é um backup que não terminou e não conta.
pub fn ultimo_backup<'a>(
    arquivos: impl IntoIterator<Item = (&'a str, SystemTime)>,
) -> Option<SystemTime> {
    arquivos
        .into_iter()
        .filter(|(nome, _)| nome.starts_with("banco_") && nome.ends_with(".dump"))
        .map(|(_, quando)| quando)
        .max()
}

/// Precisa de backup? Nunca houve, ou o último tem `intervalo` ou mais.
pub fn backup_vencido(ultimo: Option<SystemTime>, agora: SystemTime, intervalo: Duration) -> bool {
    match ultimo {
        None => true,
        // Relógio que voltou no tempo (último "no futuro"): melhor fazer um backup a mais
        Some(t) => agora
            .duration_since(t)
            .map_or(true, |idade| idade >= intervalo),
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    fn horas(h: u64) -> Duration {
        Duration::from_secs(h * 3600)
    }

    #[test]
    fn ultimo_backup_so_conta_dumps_completos() {
        let base = SystemTime::UNIX_EPOCH + horas(1000);
        let arquivos = [
            ("banco_20261013T070401Z.dump", base),
            ("banco_20261014T070401Z.dump.parcial", base + horas(30)),
            ("uploads_20261014T070401Z.tar.gz", base + horas(20)),
            ("banco_20261012T070401Z.dump", base - horas(24)),
        ];
        assert_eq!(ultimo_backup(arquivos), Some(base));
        assert_eq!(ultimo_backup([("uploads_x.tar.gz", base)]), None);
    }

    #[test]
    fn backup_vence_em_24_h() {
        let agora = SystemTime::UNIX_EPOCH + horas(1000);
        assert!(backup_vencido(None, agora, INTERVALO_BACKUP));
        assert!(!backup_vencido(
            Some(agora - horas(23)),
            agora,
            INTERVALO_BACKUP
        ));
        assert!(backup_vencido(
            Some(agora - horas(24)),
            agora,
            INTERVALO_BACKUP
        ));
        assert!(backup_vencido(
            Some(agora + horas(1)),
            agora,
            INTERVALO_BACKUP
        ));
    }
}
