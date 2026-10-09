import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type { Esquemas } from "@/lib/api";

import { duracaoDoPlano, type EstadoTimer, type Fase, MINUTO, tempoDePlano } from "./timer";

/**
 * Bloqueios do modo foco (desktop/src-tauri/src/bloqueio.rs). Regras puras + a ponte
 * para os comandos do Rust. Explicação em docs/modo-foco.md (bloqueios).
 */

export type Bloqueios = Esquemas["Bloqueios"];
export type Sistema = "macos" | "windows" | "linux" | "web";

/**
 * Folga do bloqueio de sites além do fim previsto da sessão. O prazo é a rede de
 * segurança do guardião (se tudo mais falhar, ele libera no prazo); pausas manuais
 * esticam a sessão, e o normal é liberar antes, quando a sessão termina.
 */
export const FOLGA_MIN = 60;

/** Por quantos minutos pedir o bloqueio de sites: o que falta do plano + a folga. */
export function minutosDoBloqueio(fases: Fase[], timer: EstadoTimer, agora: number): number {
  const falta = Math.max(duracaoDoPlano(fases) - tempoDePlano(timer, agora), 0);
  return Math.ceil(falta / MINUTO) + FOLGA_MIN;
}

/** O que esta sessão vai bloquear, conforme as preferências e as listas. */
export function oQueBloquear(b: Bloqueios | null, sistema: Sistema): { sites: string[]; programas: string[] } {
  if (!b) return { sites: [], programas: [] };
  const programas = sistema === "macos" || sistema === "windows" ? (b.programas?.[sistema] ?? []) : [];
  return {
    sites: b.bloquear_sites ? (b.sites ?? []) : [],
    programas: b.bloquear_programas ? programas : [],
  };
}

/** Segundos que faltam para a saída de emergência liberar (0 = pode sair). */
export function esperaRestante(pedidaEm: number, esperaS: number, agora: number): number {
  return Math.max(0, Math.ceil((pedidaEm + esperaS * 1000 - agora) / 1000));
}

/** Texto da área de texto (um item por linha) → lista, sem linhas vazias. */
export function linhas(texto: string): string[] {
  return texto
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

// A última configuração lida, para a sessão bloquear mesmo sem internet
const LEMBRADA = "estuda-ai:bloqueios";

export function lembrarBloqueios(b: Bloqueios) {
  try {
    localStorage.setItem(LEMBRADA, JSON.stringify(b));
  } catch {
    // sem localStorage: a sessão offline só não bloqueia
  }
}

export function bloqueiosLembrados(): Bloqueios | null {
  try {
    return JSON.parse(localStorage.getItem(LEMBRADA) ?? "null") as Bloqueios | null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------ ponte

/** Pede a senha (Mac) / o UAC (Windows). Erro "cancelado" = a senha não foi dada. */
export const bloquearSites = (dominios: string[], minutos: number) =>
  invoke<void>("foco_sites_bloquear", { dominios, minutos });
/** `false` = o guardião não respondeu; oferecer `restaurarSites`. */
export const liberarSites = () => invoke<boolean>("foco_sites_liberar");
export const restaurarSites = () => invoke<void>("foco_sites_restaurar");
export const situacaoSites = () =>
  invoke<{ bloqueado: boolean; guardiao_vivo: boolean }>("foco_sites_situacao");
export const iniciarProgramas = (lista: string[]) => invoke<void>("foco_programas_iniciar", { lista });
export const pararProgramas = () => invoke<void>("foco_programas_parar");

export function escutarProgramaFechado(aoFechar: (programa: string) => void): Promise<UnlistenFn> {
  return listen<{ programa: string }>("programa-fechado", (e) => aoFechar(e.payload.programa));
}
