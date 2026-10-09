import type { Esquemas } from "@/lib/api";

import type { Tocando } from "./spotify";
import type { Metodo, TipoFase } from "./timer";

/**
 * O que fazer com a música em cada momento da sessão. Funções puras (testes em
 * musica.test.ts); quem executa é a tela, chamando o Spotify pelo Rust (spotify.ts).
 */

export type ConfigSpotify = Esquemas["SpotifyEstado"];
export type NoIntervalo = ConfigSpotify["no_intervalo"];

export type AcaoMusica =
  | { tipo: "tocar"; uri: string }
  | { tipo: "pausar" }
  | { tipo: "retomar" }
  | { tipo: "nada" };

/** A playlist do foco: a da disciplina; senão a do método; senão a padrão. */
export function playlistDoFoco(config: ConfigSpotify, metodo: Metodo, disciplinaId: number | null): string | null {
  const achar = (f: (p: ConfigSpotify["playlists"][number]) => boolean) => config.playlists.find(f)?.uri ?? null;
  return (
    (disciplinaId != null ? achar((p) => p.alvo === "disciplina" && p.disciplina_id === disciplinaId) : null) ??
    achar((p) => p.alvo === "metodo" && p.metodo === metodo) ??
    achar((p) => p.alvo === "padrao")
  );
}

export function playlistDoIntervalo(config: ConfigSpotify): string | null {
  return config.playlists.find((p) => p.alvo === "intervalo")?.uri ?? null;
}

/** Momentos em que a música pode mudar. */
export type Momento =
  | { tipo: "inicio" }
  | { tipo: "fase"; de: TipoFase; para: TipoFase }
  | { tipo: "pausa_manual" }
  | { tipo: "retomada_manual" }
  | { tipo: "fim" };

export function acaoMusical(
  momento: Momento,
  config: ConfigSpotify,
  sessao: { metodo: Metodo; disciplinaId: number | null },
): AcaoMusica {
  if (!config.conectado) return { tipo: "nada" };
  const foco = playlistDoFoco(config, sessao.metodo, sessao.disciplinaId);
  const intervalo = playlistDoIntervalo(config);
  // "trocar" sem playlist de intervalo escolhida vira "pausar" (não há para onde trocar)
  const noIntervalo: NoIntervalo = config.no_intervalo === "trocar" && !intervalo ? "pausar" : config.no_intervalo;

  switch (momento.tipo) {
    case "inicio":
      return foco ? { tipo: "tocar", uri: foco } : { tipo: "nada" };
    case "pausa_manual":
      return { tipo: "pausar" };
    case "retomada_manual":
      return { tipo: "retomar" };
    case "fim":
      return { tipo: "pausar" };
    case "fase": {
      const entrouNaPausa = momento.de === "foco" && momento.para !== "foco";
      const voltouAoFoco = momento.de !== "foco" && momento.para === "foco";
      if (entrouNaPausa) {
        if (noIntervalo === "pausar") return { tipo: "pausar" };
        if (noIntervalo === "trocar") return { tipo: "tocar", uri: intervalo! };
        return { tipo: "nada" };
      }
      if (voltouAoFoco) {
        if (noIntervalo === "pausar") return { tipo: "retomar" };
        // Trocou para a do intervalo: volta para a do foco (do começo da playlist)
        if (noIntervalo === "trocar") return foco ? { tipo: "tocar", uri: foco } : { tipo: "pausar" };
        return { tipo: "nada" };
      }
      return { tipo: "nada" };
    }
  }
}

/** "https://open.spotify.com/playlist/37i9...?si=x" ou "spotify:playlist:37i9..." → URI. */
export function uriDoLink(texto: string): string | null {
  const t = texto.trim();
  const uri = t.match(/^spotify:(playlist|album|artist):([A-Za-z0-9]{22})$/);
  if (uri) return t;
  const link = t.match(/^https:\/\/open\.spotify\.com\/(?:intl-[a-z-]+\/)?(playlist|album|artist)\/([A-Za-z0-9]{22})(?:[/?#].*)?$/);
  return link ? `spotify:${link[1]}:${link[2]}` : null;
}

/** Limites da consulta do player (ms). */
export const CONSULTA = {
  /** Folga depois do fim previsto da música: o Spotify leva um instante para trocar a faixa. */
  folga: 1_000,
  minimo: 2_000,
  /**
   * Teto mesmo com a música longe do fim: a pessoa pode pular a faixa ou pausar pelo
   * próprio Spotify (no celular, por exemplo), e a tela não pode ficar errada por minutos.
   */
  maximo: 30_000,
  /** Pausada ou nada tocando: não há fim de música para esperar. */
  parado: 15_000,
};

/**
 * Daqui a quanto tempo perguntar de novo ao Spotify o que está tocando.
 *
 * Em vez de perguntar a cada 5 s (720 chamadas por hora de sessão), espera a música
 * acabar: a resposta já diz quanto falta (duracao_ms - progresso_ms). Numa música de
 * 3 min são umas 6 chamadas em vez de 36, o que importa no modo de desenvolvimento do
 * Spotify, cujo limite de chamadas é baixo e vale para o app inteiro (erro 429).
 */
export function proximaConsulta(t: Tocando | null): number {
  if (!t || !t.tocando || t.duracao_ms <= 0) return CONSULTA.parado;
  const falta = Math.max(t.duracao_ms - t.progresso_ms, 0);
  return Math.min(Math.max(falta + CONSULTA.folga, CONSULTA.minimo), CONSULTA.maximo);
}
