import { describe, expect, it } from "vitest";

import { acaoMusical, playlistDoFoco, uriDoLink, type ConfigSpotify } from "./musica";

const PADRAO = "spotify:playlist:000000000000000000000A";
const DO_5217 = "spotify:playlist:000000000000000000000B";
const DO_CALCULO = "spotify:playlist:000000000000000000000C";
const INTERVALO = "spotify:playlist:000000000000000000000D";

function config(mudancas: Partial<ConfigSpotify> = {}): ConfigSpotify {
  return {
    conectado: true, nome: "x", spotify_id: "x", conectado_em: null, no_intervalo: "pausar",
    playlists: [
      { alvo: "padrao", uri: PADRAO, nome: "Padrão" },
      { alvo: "metodo", metodo: "52_17", uri: DO_5217, nome: "52/17" },
      { alvo: "disciplina", disciplina_id: 7, uri: DO_CALCULO, nome: "Cálculo" },
      { alvo: "intervalo", uri: INTERVALO, nome: "Pausa" },
    ],
    ...mudancas,
  };
}

describe("qual playlist toca no foco", () => {
  it("disciplina > método > padrão", () => {
    expect(playlistDoFoco(config(), "52_17", 7)).toBe(DO_CALCULO);
    expect(playlistDoFoco(config(), "52_17", 99)).toBe(DO_5217);
    expect(playlistDoFoco(config(), "pomodoro", null)).toBe(PADRAO);
    expect(playlistDoFoco(config({ playlists: [] }), "pomodoro", null)).toBeNull();
  });
});

describe("o que fazer em cada momento", () => {
  const sessao = { metodo: "pomodoro" as const, disciplinaId: null };
  const pausa = { tipo: "fase", de: "foco", para: "pausa_curta" } as const;
  const volta = { tipo: "fase", de: "pausa_curta", para: "foco" } as const;

  it("começo toca a do foco; fim pausa", () => {
    expect(acaoMusical({ tipo: "inicio" }, config(), sessao)).toEqual({ tipo: "tocar", uri: PADRAO });
    expect(acaoMusical({ tipo: "fim" }, config(), sessao)).toEqual({ tipo: "pausar" });
  });

  it("intervalo 'pausar': pausa e retoma de onde parou", () => {
    expect(acaoMusical(pausa, config(), sessao)).toEqual({ tipo: "pausar" });
    expect(acaoMusical(volta, config(), sessao)).toEqual({ tipo: "retomar" });
  });

  it("intervalo 'trocar': playlist do intervalo e depois a do foco de novo", () => {
    const c = config({ no_intervalo: "trocar" });
    expect(acaoMusical(pausa, c, sessao)).toEqual({ tipo: "tocar", uri: INTERVALO });
    expect(acaoMusical(volta, c, sessao)).toEqual({ tipo: "tocar", uri: PADRAO });
  });

  it("'trocar' sem playlist de intervalo vira 'pausar'", () => {
    const c = config({ no_intervalo: "trocar", playlists: [{ alvo: "padrao", uri: PADRAO, nome: "P" }] });
    expect(acaoMusical(pausa, c, sessao)).toEqual({ tipo: "pausar" });
    expect(acaoMusical(volta, c, sessao)).toEqual({ tipo: "retomar" });
  });

  it("intervalo 'continuar': a música não muda", () => {
    const c = config({ no_intervalo: "continuar" });
    expect(acaoMusical(pausa, c, sessao)).toEqual({ tipo: "nada" });
    expect(acaoMusical(volta, c, sessao)).toEqual({ tipo: "nada" });
  });

  it("pausa e retomada manuais", () => {
    expect(acaoMusical({ tipo: "pausa_manual" }, config(), sessao)).toEqual({ tipo: "pausar" });
    expect(acaoMusical({ tipo: "retomada_manual" }, config(), sessao)).toEqual({ tipo: "retomar" });
  });

  it("sem Spotify conectado, nada", () => {
    expect(acaoMusical({ tipo: "inicio" }, config({ conectado: false }), sessao)).toEqual({ tipo: "nada" });
  });

  it("sem playlist de foco, o começo não toca nada", () => {
    expect(acaoMusical({ tipo: "inicio" }, config({ playlists: [] }), sessao)).toEqual({ tipo: "nada" });
  });
});

describe("link do Spotify para URI", () => {
  it.each([
    ["https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS?si=abc", "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"],
    ["https://open.spotify.com/intl-pt/album/4aawyAB9vmqN3uQ7FjRGTy", "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"],
    ["  spotify:playlist:37i9dQZF1DX8Uebhn9wzrS ", "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"],
  ])("%s", (texto, uri) => {
    expect(uriDoLink(texto)).toBe(uri);
  });

  it.each(["https://evil.com/playlist/37i9dQZF1DX8Uebhn9wzrS", "spotify:track:37i9dQZF1DX8Uebhn9wzrS", "abc"])(
    "%s é recusado",
    (texto) => {
      expect(uriDoLink(texto)).toBeNull();
    },
  );
});
