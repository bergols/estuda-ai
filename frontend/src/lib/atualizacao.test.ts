import { describe, expect, it } from "vitest";

import { JANELA_AUTOMATICA_MS, quandoInstalar } from "./atualizacao";

describe("quando instalar uma atualização", () => {
  it("logo ao abrir, sem sessão: instala sozinha", () => {
    expect(quandoInstalar({ desdeAbertura: 2_000, sessaoAberta: false })).toBe("agora");
    expect(quandoInstalar({ desdeAbertura: JANELA_AUTOMATICA_MS, sessaoAberta: false })).toBe("agora");
  });

  it("com sessão de estudo aberta: nunca reinicia, só avisa", () => {
    expect(quandoInstalar({ desdeAbertura: 2_000, sessaoAberta: true })).toBe("avisar");
  });

  it("encontrada com o app aberto há tempo: só avisa", () => {
    expect(quandoInstalar({ desdeAbertura: JANELA_AUTOMATICA_MS + 1, sessaoAberta: false })).toBe("avisar");
  });
});
