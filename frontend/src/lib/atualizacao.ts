import { invoke } from "@tauri-apps/api/core";

/**
 * Atualização automática do app desktop (desktop/src-tauri/src/atualizacao.rs).
 *
 * O Rust verifica, baixa e confere a assinatura; aqui só se decide QUANDO instalar.
 * Instalar reinicia o app, então:
 *  - logo ao abrir (nos primeiros segundos), instala sozinho: você ainda não começou nada;
 *  - com uma sessão de estudo aberta, nunca: avisa, e ela fica para depois;
 *  - achou mais tarde (o app ficou aberto horas): só avisa, com um botão. Reiniciar no
 *    meio de uma resposta que você está digitando perderia o texto.
 */

export type Novidade = { versao: string; notas: string | null };

/** Até quando, depois de abrir o app, uma atualização encontrada é instalada sozinha. */
export const JANELA_AUTOMATICA_MS = 15_000;
/** De quanto em quanto tempo o app aberto procura versão nova. */
export const INTERVALO_VERIFICACAO_MS = 6 * 60 * 60 * 1000;

export function quandoInstalar(p: { desdeAbertura: number; sessaoAberta: boolean }): "agora" | "avisar" {
  if (p.sessaoAberta) return "avisar";
  return p.desdeAbertura <= JANELA_AUTOMATICA_MS ? "agora" : "avisar";
}

export const verificarAtualizacao = () => invoke<Novidade | null>("atualizacao_verificar");
/** Não volta quando dá certo: o app reinicia. */
export const instalarAtualizacao = () => invoke<void>("atualizacao_instalar");
