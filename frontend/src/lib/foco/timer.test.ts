import { describe, expect, it } from "vitest";

import {
  MINUTO,
  PADROES,
  duracaoDoPlano,
  erroDaConfig,
  focoPlanejadoMs,
  iniciar,
  momento,
  pausar,
  pausasPlanejadasConcluidas,
  plano,
  relogio,
  retomar,
  statusAoEncerrar,
  tempoDePlano,
  type Config,
} from "./timer";

const T0 = Date.UTC(2026, 9, 8, 13, 0); // relógio falso: os testes passam o "agora"
const min = (n: number) => T0 + n * MINUTO;

describe("plano de cada método", () => {
  it("pomodoro: 4 focos, 3 pausas, a 4a seria longa mas a sessão acaba antes", () => {
    const fases = plano(PADROES.pomodoro);
    expect(fases.map((f) => f.tipo)).toEqual([
      "foco", "pausa_curta", "foco", "pausa_curta", "foco", "pausa_curta", "foco",
    ]);
    expect(duracaoDoPlano(fases)).toBe((4 * 25 + 3 * 5) * MINUTO);
  });

  it("pausa longa a cada N ciclos", () => {
    const c: Config = { ...PADROES.pomodoro, ciclos: 6, ciclosAtePausaLonga: 2 };
    const pausas = plano(c).filter((f) => f.tipo !== "foco").map((f) => f.tipo);
    expect(pausas).toEqual(["pausa_curta", "pausa_longa", "pausa_curta", "pausa_longa", "pausa_curta"]);
  });

  it("bloco contínuo: uma fase só, sem pausas", () => {
    const fases = plano(PADROES.bloco);
    expect(fases).toHaveLength(1);
    expect(fases[0]).toMatchObject({ tipo: "foco", duracaoMs: 60 * MINUTO });
  });

  it("52/17", () => {
    expect(plano(PADROES["52_17"]).map((f) => f.duracaoMs / MINUTO)).toEqual([52, 17, 52]);
  });

  it("personalizado com pausa 0 não cria fase de pausa vazia", () => {
    const fases = plano({ ...PADROES.personalizado, pausaMin: 0 });
    expect(fases.every((f) => f.tipo === "foco")).toBe(true);
  });

  it("foco planejado não conta as pausas", () => {
    expect(focoPlanejadoMs(PADROES.pomodoro)).toBe(100 * MINUTO);
  });
});

describe("validação igual aos CHECKs do banco", () => {
  it.each(Object.values(PADROES))("os padrões são válidos: %o", (c) => {
    expect(erroDaConfig(c)).toBeNull();
  });

  it.each<[Partial<Config>, string]>([
    [{ metodo: "52_17", focoMin: 50 }, "52/17"],
    [{ metodo: "bloco", pausaMin: 5 }, "bloco"],
    [{ focoMin: 0 }, "foco"],
    [{ focoMin: 25.5 }, "foco"],
    [{ ciclos: 13 }, "ciclos"],
    [{ pausaLongaMin: 15, ciclosAtePausaLonga: null }, "incompleta"],
  ])("%o é recusado", (mudanca, trecho) => {
    const base = mudanca.metodo ? PADROES[mudanca.metodo] : PADROES.personalizado;
    expect(erroDaConfig({ ...base, ...mudanca })).toContain(trecho);
  });
});

describe("momento: a fase atual sai de subtrações, não de ticks", () => {
  const fases = plano(PADROES.pomodoro);

  it("início", () => {
    const m = momento(fases, iniciar(T0), T0);
    expect(m).toMatchObject({ indice: 0, restanteMs: 25 * MINUTO, focoCumpridoMs: 0, terminado: false });
  });

  it("no meio da 1a pausa", () => {
    const m = momento(fases, iniciar(T0), min(27));
    expect(m.fase?.tipo).toBe("pausa_curta");
    expect(m.restanteMs).toBe(3 * MINUTO);
    expect(m.focoCumpridoMs).toBe(25 * MINUTO);
  });

  it("o notebook dormiu 3 horas: ao acordar, o plano já acabou (sem ticks perdidos)", () => {
    const m = momento(fases, iniciar(T0), min(180));
    expect(m).toMatchObject({ terminado: true, focoCumpridoMs: 100 * MINUTO, fase: null });
  });
});

describe("pausa manual congela o plano", () => {
  const fases = plano(PADROES.pomodoro);

  it("pausado, o tempo restante não anda", () => {
    const e = pausar(iniciar(T0), min(10));
    expect(momento(fases, e, min(10)).restanteMs).toBe(15 * MINUTO);
    expect(momento(fases, e, min(40)).restanteMs).toBe(15 * MINUTO);
    expect(momento(fases, e, min(40)).pausado).toBe(true);
  });

  it("retomar devolve o intervalo e o plano continua de onde parou", () => {
    const { estado, pausa } = retomar(pausar(iniciar(T0), min(10)), min(18));
    expect(pausa).toEqual({ inicioMs: min(10), fimMs: min(18) });
    expect(tempoDePlano(estado, min(20))).toBe(12 * MINUTO);
    expect(momento(fases, estado, min(20)).restanteMs).toBe(13 * MINUTO);
  });

  it("pausar duas vezes seguidas não reinicia a pausa", () => {
    const e = pausar(pausar(iniciar(T0), min(10)), min(12));
    expect(e.pausadoDesdeMs).toBe(min(10));
  });

  it("retomar sem estar pausado não cria pausa", () => {
    expect(retomar(iniciar(T0), min(5)).pausa).toBeNull();
  });
});

describe("pausas planejadas concluídas, no relógio real", () => {
  const fases = plano(PADROES.pomodoro);

  it("sem pausa manual: 25 a 30 min", () => {
    expect(pausasPlanejadasConcluidas(fases, iniciar(T0), min(31))).toEqual([
      { indice: 1, tipo: "curta", inicioMs: min(25), fimMs: min(30) },
    ]);
  });

  it("pausa em andamento ainda não conta", () => {
    expect(pausasPlanejadasConcluidas(fases, iniciar(T0), min(28))).toEqual([]);
  });

  it("uma pausa manual de 10 min antes empurra a pausa planejada no relógio", () => {
    const { estado } = retomar(pausar(iniciar(T0), min(5)), min(15));
    expect(pausasPlanejadasConcluidas(fases, estado, min(41))).toEqual([
      { indice: 1, tipo: "curta", inicioMs: min(35), fimMs: min(40) },
    ]);
  });

  it("pausa manual exatamente no fim da pausa planejada não é engolida por ela", () => {
    const { estado } = retomar(pausar(iniciar(T0), min(30)), min(33));
    const [p] = pausasPlanejadasConcluidas(fases, estado, min(40));
    expect([p.inicioMs, p.fimMs]).toEqual([min(25), min(30)]);
  });
});

describe("status ao encerrar", () => {
  const c = PADROES.pomodoro;
  const fases = plano(c);

  it("parou antes do fim: abandonada", () => {
    expect(statusAoEncerrar(fases, c, iniciar(T0), min(60))).toBe("abandonada");
  });

  it("cumpriu o foco planejado: concluída", () => {
    expect(statusAoEncerrar(fases, c, iniciar(T0), min(115))).toBe("concluida");
  });

  it("tempo pausado não conta como foco", () => {
    const { estado } = retomar(pausar(iniciar(T0), min(10)), min(40));
    expect(statusAoEncerrar(fases, c, estado, min(115))).toBe("abandonada");
    expect(statusAoEncerrar(fases, c, estado, min(145))).toBe("concluida");
  });
});

it("relógio", () => {
  expect(relogio(25 * MINUTO)).toBe("25:00");
  expect(relogio(1499)).toBe("00:02"); // arredonda para cima: nunca mostra 00:00 antes da hora
  expect(relogio(65 * MINUTO)).toBe("1:05:00");
  expect(relogio(-5)).toBe("00:00");
});
