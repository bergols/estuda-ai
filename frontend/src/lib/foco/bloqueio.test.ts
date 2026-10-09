import { describe, expect, it } from "vitest";

import { FOLGA_MIN, esperaRestante, linhas, minutosDoBloqueio, oQueBloquear, type Bloqueios } from "./bloqueio";
import { MINUTO, PADROES, iniciar, pausar, plano } from "./timer";

const T0 = Date.UTC(2026, 9, 9, 13, 0);

describe("prazo do bloqueio de sites", () => {
  const fases = plano(PADROES.pomodoro); // 4 x 25 + 3 pausas de 5 (nenhuma depois do último)
  const total = 115;

  it("no começo: o plano inteiro + a folga", () => {
    expect(minutosDoBloqueio(fases, iniciar(T0), T0)).toBe(total + FOLGA_MIN);
  });

  it("continuando depois de 40 min: só o que falta", () => {
    expect(minutosDoBloqueio(fases, iniciar(T0), T0 + 40 * MINUTO)).toBe(total - 40 + FOLGA_MIN);
  });

  it("tempo pausado não conta como plano cumprido", () => {
    const pausado = pausar(iniciar(T0), T0 + 10 * MINUTO);
    expect(minutosDoBloqueio(fases, pausado, T0 + 50 * MINUTO)).toBe(total - 10 + FOLGA_MIN);
  });
});

describe("o que bloquear", () => {
  const b: Bloqueios = {
    bloquear_sites: true, bloquear_programas: false, espera_emergencia_s: 60,
    sites: ["youtube.com"], programas: { macos: ["Discord"], windows: ["Discord.exe"] },
  };

  it("respeita as chaves liga/desliga e o sistema", () => {
    expect(oQueBloquear(b, "macos")).toEqual({ sites: ["youtube.com"], programas: [] });
    expect(oQueBloquear({ ...b, bloquear_programas: true }, "windows")).toEqual({
      sites: ["youtube.com"], programas: ["Discord.exe"],
    });
    expect(oQueBloquear({ ...b, bloquear_sites: false }, "macos").sites).toEqual([]);
    expect(oQueBloquear(null, "macos")).toEqual({ sites: [], programas: [] });
  });
});

describe("saída de emergência", () => {
  it("conta a espera e nunca fica negativa", () => {
    expect(esperaRestante(T0, 60, T0)).toBe(60);
    expect(esperaRestante(T0, 60, T0 + 59_001)).toBe(1);
    expect(esperaRestante(T0, 60, T0 + 60_000)).toBe(0);
    expect(esperaRestante(T0, 60, T0 + 999_000)).toBe(0);
  });
});

it("lista da área de texto", () => {
  expect(linhas(" youtube.com \r\n\n reddit.com\n")).toEqual(["youtube.com", "reddit.com"]);
});
