import { describe, expect, it } from "vitest";

import {
  SAIDA_MINIMA_MS,
  bater,
  continuarAposFechamento,
  encerrar,
  encerrarAposFechamento,
  precisaBatida,
  novaSessao,
  pausarSessao,
  registrarPausasPlanejadas,
  retomarSessao,
  saiuDaJanela,
  voltouParaJanela,
  type SessaoAtiva,
} from "./sessao";
import { MINUTO, PADROES } from "./timer";

const T0 = Date.UTC(2026, 9, 8, 13, 0);
const min = (n: number) => T0 + n * MINUTO;

function chaves() {
  let n = 0;
  return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
}

function pomodoro(): { s: SessaoAtiva; chave: () => string } {
  const chave = chaves();
  const s = novaSessao({ config: PADROES.pomodoro, disciplinaId: 7, meta: "  lista 3 ", sistema: "macos", agora: T0, novaChave: chave });
  return { s, chave };
}

describe("sessão no formato da API", () => {
  it("nova sessão", () => {
    const { s } = pomodoro();
    expect(s.dados).toMatchObject({
      metodo: "pomodoro", foco_min: 25, pausa_min: 5, ciclos: 4, pausa_longa_min: 15,
      ciclos_ate_pausa_longa: 4, meta: "lista 3", disciplina_id: 7, status: "em_andamento",
      iniciada_em: "2026-10-08T13:00:00.000Z", terminada_em: null, pausas: [], eventos: [],
    });
  });

  it("meta só com espaços vira null (o CHECK do banco recusaria)", () => {
    const s = novaSessao({ config: PADROES.bloco, disciplinaId: null, meta: "   ", sistema: "windows", agora: T0, novaChave: chaves() });
    expect(s.dados.meta).toBeNull();
  });
});

describe("pausas planejadas", () => {
  it("registra cada uma UMA vez, com a mesma chave para sempre", () => {
    const { s, chave } = pomodoro();
    const a = registrarPausasPlanejadas(s, min(31), chave);
    expect(a.dados.pausas).toEqual([{
      chave: "00000000-0000-4000-8000-000000000002", tipo: "curta",
      iniciada_em: "2026-10-08T13:25:00.000Z", terminada_em: "2026-10-08T13:30:00.000Z",
    }]);
    // Ticks seguintes: nada novo, e a MESMA referência (a tela não grava à toa)
    expect(registrarPausasPlanejadas(a, min(32), chave)).toBe(a);
  });
});

describe("pausa manual", () => {
  it("vira uma pausa 'manual' ao retomar", () => {
    const { s, chave } = pomodoro();
    const r = retomarSessao(pausarSessao(s, min(10)), min(14), chave);
    expect(r.dados.pausas).toEqual([expect.objectContaining({
      tipo: "manual", iniciada_em: "2026-10-08T13:10:00.000Z", terminada_em: "2026-10-08T13:14:00.000Z",
    })]);
  });

  it("não pausa durante uma pausa planejada", () => {
    const { s } = pomodoro();
    expect(pausarSessao(s, min(27))).toBe(s);
  });
});

describe("saída da janela", () => {
  it("vira evento com a duração", () => {
    const { s, chave } = pomodoro();
    const r = voltouParaJanela(saiuDaJanela(s, min(5)), min(7), chave);
    expect(r.dados.eventos).toEqual([expect.objectContaining({
      tipo: "saida_janela", ocorrido_em: "2026-10-08T13:05:00.000Z", duracao_s: 120,
    })]);
    expect(r.local.foraDesdeMs).toBeNull();
  });

  it("alt-tab rápido não conta", () => {
    const { s, chave } = pomodoro();
    const r = voltouParaJanela(saiuDaJanela(s, min(5)), min(5) + SAIDA_MINIMA_MS - 1, chave);
    expect(r.dados.eventos).toEqual([]);
  });

  it("sair da janela na pausa é o esperado: não conta", () => {
    const { s } = pomodoro();
    expect(saiuDaJanela(s, min(27))).toBe(s);
  });
});

describe("encerrar", () => {
  it("antes do fim: abandonada, com a emergência registrada e o que estava aberto fechado", () => {
    const { s, chave } = pomodoro();
    let atual = saiuDaJanela(s, min(40));
    atual = encerrar(atual, min(45), chave, { emergencia: true });
    expect(atual.dados.status).toBe("abandonada");
    expect(atual.dados.terminada_em).toBe("2026-10-08T13:45:00.000Z");
    expect(atual.dados.pausas).toHaveLength(1); // a pausa planejada de 25 a 30
    expect(atual.dados.eventos?.map((e) => e.tipo)).toEqual(["saida_janela", "saida_emergencia"]);
  });

  it("plano cumprido: concluída", () => {
    const { s, chave } = pomodoro();
    const fim = encerrar(s, min(115), chave);
    expect(fim.dados.status).toBe("concluida");
    expect(fim.dados.pausas).toHaveLength(3);
  });

  it("encerrar pausado fecha a pausa manual", () => {
    const { s, chave } = pomodoro();
    const fim = encerrar(pausarSessao(s, min(10)), min(20), chave);
    expect(fim.dados.pausas?.map((p) => p.tipo)).toEqual(["manual"]);
    expect(fim.local.timer.pausadoDesdeMs).toBeNull();
  });
});

describe("app fechado ou travado no meio", () => {
  it("batida de vida a cada 30 s", () => {
    const { s } = pomodoro();
    expect(precisaBatida(s, T0 + 29_000)).toBe(false);
    expect(precisaBatida(s, T0 + 30_000)).toBe(true);
    expect(precisaBatida(bater(s, T0 + 30_000), T0 + 31_000)).toBe(false);
  });

  it("continuar: o tempo fechado vira pausa manual, não foco", () => {
    const { s, chave } = pomodoro();
    const visto = bater(s, min(10)); // último sinal aos 10 min; o app fechou
    const r = continuarAposFechamento(visto, min(70), chave); // reaberto 1 h depois
    expect(r.dados.pausas).toEqual([expect.objectContaining({
      tipo: "manual", iniciada_em: "2026-10-08T13:10:00.000Z", terminada_em: "2026-10-08T14:10:00.000Z",
    })]);
    // O timer volta de onde parou: 15 min restantes no 1o foco
    expect(r.local.timer.pausadoDesdeMs).toBeNull();
  });

  it("encerrar: no último sinal de vida (3 h fechado não viram 3 h de foco)", () => {
    const { s, chave } = pomodoro();
    const fim = encerrarAposFechamento(bater(s, min(40)), chave);
    expect(fim.dados.terminada_em).toBe("2026-10-08T13:40:00.000Z");
    expect(fim.dados.status).toBe("abandonada");
  });
});
