import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type { EstadoLocal, SessaoAtiva, SessaoEnvio } from "./sessao";

/**
 * Ponte para a fila local do app desktop (desktop/src-tauri/src/sincronia.rs).
 *
 * A tela grava a sessão a CADA mudança (início, pausa, saída da janela, fim) e nunca
 * espera a rede: o Rust guarda no SQLite e um laço em segundo plano manda ao servidor.
 * Se o app fechar ou travar, a sessão em andamento volta pelo `sessaoEmAndamento`.
 */

export type SituacaoSincronia = { pendentes: number; recusadas: number; erro?: string | null };

export function salvarSessao(s: SessaoAtiva): Promise<number> {
  return invoke<number>("salvar_sessao", { chave: s.dados.chave, dados: s.dados, timer: s.local });
}

export async function sessaoEmAndamento(): Promise<SessaoAtiva | null> {
  const local = await invoke<{ dados: SessaoEnvio; timer: EstadoLocal | null } | null>("sessao_em_andamento");
  if (!local || !local.timer) return null;
  return { dados: local.dados, local: local.timer };
}

export function situacaoSincronia(): Promise<SituacaoSincronia> {
  return invoke<SituacaoSincronia>("situacao_sincronia");
}

export function sincronizarAgora(): Promise<void> {
  return invoke("sincronizar_agora");
}

/** Avisos do laço de envio depois de cada tentativa. */
export function escutarSincronia(aoMudar: (s: SituacaoSincronia) => void): Promise<UnlistenFn> {
  return listen<SituacaoSincronia>("sincronia", (evento) => aoMudar(evento.payload));
}
