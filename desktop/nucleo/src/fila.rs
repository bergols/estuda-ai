//! Fila local das sessões de estudo: um SQLite no computador (modo offline).
//!
//! Tudo o que acontece numa sessão (início, pausas, eventos, fim) é gravado AQUI
//! primeiro, sem depender de rede. Um laço em segundo plano (src-tauri/src/sincronia.rs)
//! manda ao servidor o que ainda não teve confirmação. Se o servidor estiver fora do
//! ar, a sessão continua normalmente e o envio fica para depois.
//!
//! O protocolo é o mais simples possível, e só é seguro porque o servidor é
//! IDEMPOTENTE: "mande a sessão inteira (com todas as pausas e eventos) sempre que ela
//! mudou desde o último envio confirmado". Reenviar o que o servidor já tem não
//! duplica nada (chaves de idempotência + ON CONFLICT, ver backend/app/servicos/sessoes.py).
//!
//! Versões em vez de "enviada sim/não": cada gravação local incrementa `versao`. O
//! envio leva a versão que leu; a confirmação grava `versao_enviada` com ELA. Se a
//! sessão mudou durante o envio (um evento chegou no meio), `versao` > `versao_enviada`
//! e ela continua pendente. Um booleano "enviada = 1" perderia essa mudança.

use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::Value;

pub struct Fila {
    conexao: Connection,
}

/// Uma sessão pendente de envio: os dados no formato da API e a versão lida.
#[derive(Debug, Clone, PartialEq)]
pub struct Pendente {
    pub chave: String,
    pub versao: i64,
    pub dados: Value,
}

/// O que o servidor respondeu sobre uma sessão.
#[derive(Debug, Clone, PartialEq)]
pub enum Veredito {
    /// Gravada (criada, atualizada ou já existia): não precisa reenviar esta versão.
    Aceita,
    /// Violou uma regra do servidor: reenviar a MESMA versão não adianta (falharia de
    /// novo para sempre). Fica registrada com o erro; só volta à fila se mudar.
    Recusada(String),
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Situacao {
    pub pendentes: i64,
    pub recusadas: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SessaoLocal {
    pub dados: Value,
    pub timer: Option<Value>,
}

impl Fila {
    pub fn abrir(caminho: &std::path::Path) -> rusqlite::Result<Self> {
        Self::preparar(Connection::open(caminho)?)
    }

    pub fn em_memoria() -> rusqlite::Result<Self> {
        Self::preparar(Connection::open_in_memory()?)
    }

    fn preparar(conexao: Connection) -> rusqlite::Result<Self> {
        // WAL: quem lê não bloqueia quem escreve (a janela grava eventos enquanto o laço
        // de sincronização lê a fila). synchronous=NORMAL é seguro com WAL: numa queda de
        // energia perde-se no máximo a última transação, nunca o arquivo.
        conexao.pragma_update(None, "journal_mode", "WAL")?;
        conexao.pragma_update(None, "synchronous", "NORMAL")?;
        conexao.execute_batch(
            "CREATE TABLE IF NOT EXISTS sessoes (
                 chave          TEXT PRIMARY KEY,
                 -- servidor de destino: a sessão feita contra o localhost nunca vai
                 -- para a produção (mesma ideia do token por origem no cofre)
                 origem         TEXT NOT NULL,
                 dados          TEXT NOT NULL CHECK (json_valid(dados)),
                 timer          TEXT CHECK (timer IS NULL OR json_valid(timer)),
                 versao         INTEGER NOT NULL DEFAULT 1,
                 versao_enviada INTEGER,
                 erro           TEXT,
                 alterada_em    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
             ) STRICT;
             CREATE INDEX IF NOT EXISTS ix_sessoes_pendentes
                 ON sessoes (origem, versao_enviada);",
        )?;
        Ok(Self { conexao })
    }

    /// Grava o estado atual da sessão (UPSERT do SQLite, a mesma ideia do ON CONFLICT
    /// do Postgres) e a torna pendente de novo.
    pub fn salvar(
        &self,
        origem: &str,
        chave: &str,
        dados: &Value,
        timer: Option<&Value>,
    ) -> rusqlite::Result<i64> {
        self.conexao.query_row(
            "INSERT INTO sessoes (chave, origem, dados, timer) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT (chave) DO UPDATE
                 SET dados = excluded.dados,
                     timer = excluded.timer,
                     versao = sessoes.versao + 1,
                     erro = NULL,
                     alterada_em = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             RETURNING versao",
            params![
                chave,
                origem,
                dados.to_string(),
                timer.map(Value::to_string)
            ],
            |linha| linha.get(0),
        )
    }

    /// Sessões que mudaram desde o último envio confirmado (as mais antigas primeiro).
    pub fn pendentes(&self, origem: &str, limite: usize) -> rusqlite::Result<Vec<Pendente>> {
        let mut consulta = self.conexao.prepare(
            "SELECT chave, versao, dados FROM sessoes
             WHERE origem = ?1
               AND (versao_enviada IS NULL OR versao_enviada < versao)
             ORDER BY alterada_em
             LIMIT ?2",
        )?;
        let linhas = consulta.query_map(params![origem, limite as i64], |linha| {
            let dados: String = linha.get(2)?;
            Ok(Pendente {
                chave: linha.get(0)?,
                versao: linha.get(1)?,
                dados: serde_json::from_str(&dados).unwrap_or(Value::Null),
            })
        })?;
        linhas.collect()
    }

    /// Registra a resposta do servidor para a versão que foi ENVIADA.
    ///
    /// MAX(): se uma resposta antiga chegar depois de uma nova (duas tentativas
    /// sobrepostas), ela não faz a versão confirmada andar para trás.
    pub fn confirmar(&self, chave: &str, versao: i64, veredito: &Veredito) -> rusqlite::Result<()> {
        let erro = match veredito {
            Veredito::Aceita => None,
            Veredito::Recusada(motivo) => Some(motivo.as_str()),
        };
        self.conexao.execute(
            "UPDATE sessoes
             SET versao_enviada = max(coalesce(versao_enviada, 0), ?2),
                 erro = CASE WHEN versao = ?2 THEN ?3 ELSE erro END
             WHERE chave = ?1",
            params![chave, versao, erro],
        )?;
        Ok(())
    }

    pub fn situacao(&self, origem: &str) -> rusqlite::Result<Situacao> {
        self.conexao.query_row(
            "SELECT count(*) FILTER (WHERE versao_enviada IS NULL OR versao_enviada < versao),
                    count(*) FILTER (WHERE erro IS NOT NULL)
             FROM sessoes WHERE origem = ?1",
            params![origem],
            |linha| {
                Ok(Situacao {
                    pendentes: linha.get(0)?,
                    recusadas: linha.get(1)?,
                })
            },
        )
    }

    /// A sessão que ficou em andamento (o app fechou ou travou no meio): a janela
    /// oferece retomar ou encerrar.
    pub fn em_andamento(&self, origem: &str) -> rusqlite::Result<Option<SessaoLocal>> {
        self.conexao
            .query_row(
                "SELECT dados, timer FROM sessoes
                 WHERE origem = ?1 AND dados ->> '$.status' = 'em_andamento'
                 ORDER BY alterada_em DESC
                 LIMIT 1",
                params![origem],
                |linha| {
                    let dados: String = linha.get(0)?;
                    let timer: Option<String> = linha.get(1)?;
                    Ok(SessaoLocal {
                        dados: serde_json::from_str(&dados).unwrap_or(Value::Null),
                        timer: timer.and_then(|t| serde_json::from_str(&t).ok()),
                    })
                },
            )
            .optional()
    }
}

#[cfg(test)]
mod testes {
    use super::*;
    use serde_json::json;

    const ORIGEM: &str = "https://estuda-ai.example.com";

    fn sessao(status: &str) -> Value {
        json!({ "chave": "c1", "status": status, "pausas": [], "eventos": [] })
    }

    #[test]
    fn sessao_salva_fica_pendente() {
        let fila = Fila::em_memoria().unwrap();
        assert_eq!(
            fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
                .unwrap(),
            1
        );
        let pendentes = fila.pendentes(ORIGEM, 50).unwrap();
        assert_eq!(pendentes.len(), 1);
        assert_eq!(pendentes[0].versao, 1);
        assert_eq!(pendentes[0].dados["status"], "em_andamento");
    }

    #[test]
    fn confirmada_sai_da_fila_e_volta_se_mudar() {
        let fila = Fila::em_memoria().unwrap();
        fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
            .unwrap();
        fila.confirmar("c1", 1, &Veredito::Aceita).unwrap();
        assert!(fila.pendentes(ORIGEM, 50).unwrap().is_empty());

        // Um evento novo: versão 2, pendente de novo (e o envio leva a sessão inteira)
        assert_eq!(
            fila.salvar(ORIGEM, "c1", &sessao("concluida"), None)
                .unwrap(),
            2
        );
        let pendentes = fila.pendentes(ORIGEM, 50).unwrap();
        assert_eq!((pendentes.len(), pendentes[0].versao), (1, 2));
    }

    #[test]
    fn mudanca_durante_o_envio_nao_se_perde() {
        let fila = Fila::em_memoria().unwrap();
        fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
            .unwrap();
        let enviada = fila.pendentes(ORIGEM, 50).unwrap().remove(0); // lê a versão 1
        fila.salvar(ORIGEM, "c1", &sessao("concluida"), None)
            .unwrap(); // versão 2, no meio do envio
        fila.confirmar(&enviada.chave, enviada.versao, &Veredito::Aceita)
            .unwrap(); // confirma a 1
        // A 2 continua pendente: um "enviada = true" teria perdido a conclusão
        let pendentes = fila.pendentes(ORIGEM, 50).unwrap();
        assert_eq!(pendentes[0].versao, 2);
        assert_eq!(pendentes[0].dados["status"], "concluida");
    }

    #[test]
    fn confirmacao_atrasada_nao_volta_a_versao() {
        let fila = Fila::em_memoria().unwrap();
        fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
            .unwrap();
        fila.salvar(ORIGEM, "c1", &sessao("concluida"), None)
            .unwrap();
        fila.confirmar("c1", 2, &Veredito::Aceita).unwrap();
        fila.confirmar("c1", 1, &Veredito::Aceita).unwrap(); // resposta antiga chegando tarde
        assert!(fila.pendentes(ORIGEM, 50).unwrap().is_empty());
    }

    #[test]
    fn recusada_nao_e_reenviada_mas_fica_registrada() {
        let fila = Fila::em_memoria().unwrap();
        fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
            .unwrap();
        fila.confirmar("c1", 1, &Veredito::Recusada("ck_x".into()))
            .unwrap();
        assert!(fila.pendentes(ORIGEM, 50).unwrap().is_empty());
        assert_eq!(
            fila.situacao(ORIGEM).unwrap(),
            Situacao {
                pendentes: 0,
                recusadas: 1
            }
        );
        // Mudou de novo: volta à fila, sem o erro antigo
        fila.salvar(ORIGEM, "c1", &sessao("concluida"), None)
            .unwrap();
        assert_eq!(
            fila.situacao(ORIGEM).unwrap(),
            Situacao {
                pendentes: 1,
                recusadas: 0
            }
        );
    }

    #[test]
    fn cada_servidor_tem_a_sua_fila() {
        let fila = Fila::em_memoria().unwrap();
        fila.salvar(
            "http://localhost:3000",
            "dev",
            &sessao("em_andamento"),
            None,
        )
        .unwrap();
        assert!(fila.pendentes(ORIGEM, 50).unwrap().is_empty());
        assert_eq!(
            fila.pendentes("http://localhost:3000", 50).unwrap().len(),
            1
        );
    }

    #[test]
    fn sessao_em_andamento_volta_com_o_estado_do_timer() {
        let fila = Fila::em_memoria().unwrap();
        let timer = json!({ "pausadoEm": null });
        fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), Some(&timer))
            .unwrap();
        let local = fila.em_andamento(ORIGEM).unwrap().unwrap();
        assert_eq!(local.timer, Some(timer));
        fila.salvar(ORIGEM, "c1", &sessao("concluida"), None)
            .unwrap();
        assert_eq!(fila.em_andamento(ORIGEM).unwrap(), None);
    }

    #[test]
    fn json_invalido_e_recusado_pelo_proprio_sqlite() {
        let fila = Fila::em_memoria().unwrap();
        let erro = fila.conexao.execute(
            "INSERT INTO sessoes (chave, origem, dados) VALUES ('x', 'o', 'não é json')",
            [],
        );
        assert!(erro.is_err());
    }

    #[test]
    fn sobrevive_a_reabrir_o_arquivo() {
        let pasta = std::env::temp_dir().join(format!("fila-teste-{}", std::process::id()));
        std::fs::create_dir_all(&pasta).unwrap();
        let caminho = pasta.join("estuda-ai.db");
        {
            let fila = Fila::abrir(&caminho).unwrap();
            fila.salvar(ORIGEM, "c1", &sessao("em_andamento"), None)
                .unwrap();
        } // "o app fechou"
        let fila = Fila::abrir(&caminho).unwrap();
        assert_eq!(fila.pendentes(ORIGEM, 50).unwrap().len(), 1);
        drop(fila);
        std::fs::remove_dir_all(&pasta).unwrap();
    }
}
