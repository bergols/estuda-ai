//! Sessões de estudo: gravação local (SQLite) e envio ao servidor em segundo plano.
//!
//! A janela grava cada mudança da sessão com `salvar_sessao` (nunca espera a rede).
//! Um laço manda ao servidor o que está pendente: logo depois de cada gravação e, de
//! qualquer forma, a cada 30 s. Sem internet ou com o servidor fora do ar, a fila só
//! cresce; quando a conexão volta, tudo sobe sozinho. Ver nucleo::fila e
//! docs/modo-foco.md, "Sincronização offline".

use std::sync::Mutex;
use std::time::Duration;

use nucleo::fila::{Fila, SessaoLocal, Situacao, Veredito};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::ponte::{self, Estado};

const INTERVALO: Duration = Duration::from_secs(30);
const LOTE: usize = 50; // o máximo que a API aceita por envio

pub struct Sincronia {
    fila: Mutex<Fila>,
    // Um envio por vez: duas tentativas sobrepostas mandariam o mesmo lote em dobro
    // (inofensivo, por causa da idempotência, mas desperdício)
    enviando: tokio::sync::Mutex<()>,
    // Acorda o laço antes dos 30 s (logo depois de uma gravação)
    acordar: tokio::sync::Notify,
}

impl Sincronia {
    pub fn abrir(app: &AppHandle) -> Result<Self, String> {
        let pasta = app.path().app_data_dir().map_err(|e| e.to_string())?;
        std::fs::create_dir_all(&pasta).map_err(|e| e.to_string())?;
        let fila = Fila::abrir(&pasta.join("estuda-ai.db")).map_err(|e| e.to_string())?;
        Ok(Self::com(fila))
    }

    fn com(fila: Fila) -> Self {
        Self {
            fila: Mutex::new(fila),
            enviando: tokio::sync::Mutex::new(()),
            acordar: tokio::sync::Notify::new(),
        }
    }

    fn fila(&self) -> std::sync::MutexGuard<'_, Fila> {
        // Um painel anterior que entrou em pânico com o lock não deve travar o app
        self.fila.lock().unwrap_or_else(|e| e.into_inner())
    }
}

/// Avisado à janela depois de cada tentativa (o rodapé "3 sessões aguardando conexão").
#[derive(Clone, Serialize)]
pub struct EstadoSincronia {
    #[serde(flatten)]
    situacao: Situacao,
    erro: Option<String>,
}

#[derive(Deserialize)]
struct ResultadoSessao {
    chave: String,
    resultado: String,
    erro: Option<String>,
}

#[derive(Deserialize)]
struct ResultadoSincronizacao {
    sessoes: Vec<ResultadoSessao>,
}

/// Começa o laço de envio (chamado uma vez, no início do app).
pub fn iniciar(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        loop {
            sincronizar(&app).await;
            let sincronia = app.state::<Sincronia>();
            // Dorme até 30 s ou até alguém gravar algo novo, o que vier primeiro
            let _ = tokio::time::timeout(INTERVALO, sincronia.acordar.notified()).await;
        }
    });
}

async fn sincronizar(app: &AppHandle) {
    let sincronia = app.state::<Sincronia>();
    let estado = app.state::<Estado>();
    let erro = enviar_pendentes(&sincronia, &estado).await;
    if let Ok(situacao) = sincronia.fila().situacao(&estado.origem()) {
        let _ = app.emit("sincronia", EstadoSincronia { situacao, erro });
    }
}

/// Manda tudo o que está pendente, em lotes, e registra a resposta de cada sessão.
/// Devolve o erro que interrompeu o envio (sem rede, sem login...), se houver; o que
/// não foi confirmado continua na fila. Separado do laço para ser testado sem janela.
async fn enviar_pendentes(sincronia: &Sincronia, estado: &Estado) -> Option<String> {
    let _vez = sincronia.enviando.lock().await;
    let origem = estado.origem();

    let mut erro = None;
    loop {
        // Lê e solta o lock ANTES da rede: a janela continua gravando enquanto envia
        let pendentes = match sincronia.fila().pendentes(&origem, LOTE) {
            Ok(p) => p,
            Err(e) => {
                erro = Some(format!("fila local: {e}"));
                break;
            }
        };
        if pendentes.is_empty() {
            break;
        }
        let corpo = serde_json::json!({
            "sessoes": pendentes.iter().map(|p| p.dados.clone()).collect::<Vec<Value>>()
        });
        let resposta = match ponte::chamar(
            estado,
            "POST",
            "sessoes/sincronizar",
            Some("application/json".into()),
            corpo.to_string().into_bytes(),
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                erro = Some(e);
                break;
            }
        };

        match resposta.status() {
            200 => {
                let Ok(resultado) =
                    serde_json::from_str::<ResultadoSincronizacao>(resposta.corpo())
                else {
                    erro = Some("resposta inesperada do servidor".into());
                    break;
                };
                let fila = sincronia.fila();
                let mut confirmadas = 0;
                for item in resultado.sessoes {
                    let Some(enviada) = pendentes.iter().find(|p| p.chave == item.chave) else {
                        continue;
                    };
                    let veredito = if item.resultado == "recusada" {
                        Veredito::Recusada(item.erro.unwrap_or_else(|| "recusada".into()))
                    } else {
                        Veredito::Aceita
                    };
                    match fila.confirmar(&enviada.chave, enviada.versao, &veredito) {
                        Ok(()) => confirmadas += 1,
                        Err(e) => erro = Some(format!("fila local: {e}")),
                    }
                }
                // Último lote, ou nenhum progresso (evita repetir o mesmo lote em laço)
                if pendentes.len() < LOTE || confirmadas == 0 {
                    break;
                }
            }
            // Formato recusado pela API (bug do app, não falta de rede): reenviar não
            // resolveria. Marca o lote como recusado para não ficar em loop para sempre.
            422 => {
                let fila = sincronia.fila();
                for p in &pendentes {
                    let _ = fila.confirmar(
                        &p.chave,
                        p.versao,
                        &Veredito::Recusada("formato recusado pela API".into()),
                    );
                }
                erro = Some("o servidor recusou o formato de algumas sessões".into());
                break;
            }
            // 401 (sem login), 503 (offline), 5xx: fica tudo na fila para a próxima vez
            401 => {
                erro = Some("entre na sua conta para sincronizar".into());
                break;
            }
            _ => {
                erro = Some(resposta.detalhe());
                break;
            }
        }
    }

    erro
}

// ------------------------------------------------------------------- comandos

/// Grava o estado atual da sessão (dados no formato da API + estado interno do timer)
/// e acorda o laço de envio. Não espera a rede.
#[tauri::command]
pub fn salvar_sessao(
    chave: String,
    dados: Value,
    timer: Option<Value>,
    sincronia: State<'_, Sincronia>,
    estado: State<'_, Estado>,
) -> Result<i64, String> {
    // A chave da fila e a do corpo enviado à API têm de ser a mesma
    if dados.get("chave").and_then(Value::as_str) != Some(chave.as_str()) {
        return Err("chave da sessão inconsistente".into());
    }
    let versao = sincronia
        .fila()
        .salvar(&estado.origem(), &chave, &dados, timer.as_ref())
        .map_err(|e| e.to_string())?;
    sincronia.acordar.notify_one();
    Ok(versao)
}

/// A sessão que ficou em andamento (app fechado ou travado no meio), se houver.
#[tauri::command]
pub fn sessao_em_andamento(
    sincronia: State<'_, Sincronia>,
    estado: State<'_, Estado>,
) -> Result<Option<SessaoLocal>, String> {
    sincronia
        .fila()
        .em_andamento(&estado.origem())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn situacao_sincronia(
    sincronia: State<'_, Sincronia>,
    estado: State<'_, Estado>,
) -> Result<Situacao, String> {
    sincronia
        .fila()
        .situacao(&estado.origem())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn sincronizar_agora(sincronia: State<'_, Sincronia>) {
    sincronia.acordar.notify_one();
}

#[cfg(test)]
mod testes {
    use super::*;
    use serde_json::json;

    fn chave() -> String {
        uuid::Uuid::new_v4().to_string()
    }

    /// Sessão no formato da API, com um evento (de chave fixa) de saída da janela.
    fn sessao(chave: &str, evento: &str, status: &str) -> Value {
        let terminada = if status == "em_andamento" {
            Value::Null
        } else {
            json!("2026-10-08T13:25:00Z")
        };
        json!({
            "chave": chave, "metodo": "pomodoro", "foco_min": 25, "pausa_min": 5, "ciclos": 1,
            "sistema": "macos", "status": status, "iniciada_em": "2026-10-08T13:00:00Z",
            "terminada_em": terminada, "pausas": [],
            "eventos": [{ "chave": evento, "tipo": "saida_janela",
                          "ocorrido_em": "2026-10-08T13:05:00Z", "duracao_s": 40 }]
        })
    }

    /// Sem servidor (porta 9 do localhost recusa na hora): nada se perde, tudo fica na fila.
    #[tokio::test]
    async fn sem_servidor_a_fila_guarda_tudo() {
        let estado = Estado::com_endereco("http://127.0.0.1:9").unwrap();
        let sincronia = Sincronia::com(Fila::em_memoria().unwrap());
        let c = chave();
        sincronia
            .fila()
            .salvar(
                &estado.origem(),
                &c,
                &sessao(&c, &chave(), "concluida"),
                None,
            )
            .unwrap();
        // sem token no cofre para este servidor: o envio para antes da rede
        assert!(enviar_pendentes(&sincronia, &estado).await.is_some());
        assert_eq!(
            sincronia
                .fila()
                .situacao(&estado.origem())
                .unwrap()
                .pendentes,
            1
        );
    }

    /// De ponta a ponta: SQLite → BFF local → API → Postgres, com o Keychain de verdade.
    /// Os testes ignorados usam o MESMO item do cofre (o do localhost): rode em série.
    ///   ESTUDA_AI_TESTE_SENHA=... cargo test -p estuda-ai -- --ignored --test-threads=1
    #[tokio::test]
    #[ignore]
    async fn sqlite_ate_o_postgres_sem_duplicar() {
        let senha = std::env::var("ESTUDA_AI_TESTE_SENHA").expect("defina ESTUDA_AI_TESTE_SENHA");
        let estado = Estado::com_endereco("http://localhost:3000").unwrap();
        let r = ponte::entrar_com(&estado, "estudante@estuda-ai.local", &senha)
            .await
            .unwrap();
        assert_eq!(r.status(), 204);

        let sincronia = Sincronia::com(Fila::em_memoria().unwrap());
        let origem = estado.origem();
        let fila = || sincronia.fila();
        let (a, ea, b, eb) = (chave(), chave(), chave(), chave());
        fila()
            .salvar(&origem, &a, &sessao(&a, &ea, "em_andamento"), None)
            .unwrap();
        fila()
            .salvar(&origem, &b, &sessao(&b, &eb, "concluida"), None)
            .unwrap();

        assert_eq!(enviar_pendentes(&sincronia, &estado).await, None);
        assert_eq!(fila().situacao(&origem).unwrap().pendentes, 0);

        // A sessão "a" termina: só ela volta à fila e sobe de novo
        fila()
            .salvar(&origem, &a, &sessao(&a, &ea, "concluida"), None)
            .unwrap();
        assert_eq!(fila().pendentes(&origem, 50).unwrap().len(), 1);
        assert_eq!(enviar_pendentes(&sincronia, &estado).await, None);

        // Reenvio de tudo (como se as confirmações tivessem se perdido): o servidor
        // responde "sem_mudanca" e nenhum evento é gravado de novo
        let lote =
            json!({ "sessoes": [sessao(&a, &ea, "concluida"), sessao(&b, &eb, "concluida")] });
        let r = ponte::chamar(
            &estado,
            "POST",
            "sessoes/sincronizar",
            Some("application/json".into()),
            lote.to_string().into_bytes(),
        )
        .await
        .unwrap();
        assert_eq!(r.status(), 200, "{}", r.corpo());
        let corpo: Value = serde_json::from_str(r.corpo()).unwrap();
        for item in corpo["sessoes"].as_array().unwrap() {
            assert_eq!(item["resultado"], "sem_mudanca");
            assert_eq!(item["eventos_novos"], 0);
        }

        ponte::sair_de(&estado).unwrap();
    }
}
