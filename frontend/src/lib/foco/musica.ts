import type { Esquemas } from "@/lib/api";

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
