import { describe, expect, it } from "vitest";

import { enfatizar } from "./enfase";

describe("ênfase mínima", () => {
  it("negrito, itálico e código", () => {
    expect(enfatizar("um **heap** é *completo* com `heap-size`")).toEqual([
      { tipo: "texto", valor: "um " },
      { tipo: "negrito", valor: "heap" },
      { tipo: "texto", valor: " é " },
      { tipo: "italico", valor: "completo" },
      { tipo: "texto", valor: " com " },
      { tipo: "codigo", valor: "heap-size" },
    ]);
  });

  it("asterisco solto não vira itálico", () => {
    expect(enfatizar("custa 2 * 3 e a*b")).toEqual([{ tipo: "texto", valor: "custa 2 * 3 e a*b" }]);
  });

  it("texto sem marcas passa inteiro; HTML continua texto", () => {
    expect(enfatizar("<script>x</script>")).toEqual([{ tipo: "texto", valor: "<script>x</script>" }]);
    expect(enfatizar("")).toEqual([]);
  });

  it("negrito com várias palavras e pontuação", () => {
    expect(enfatizar("não **é estável**.")).toEqual([
      { tipo: "texto", valor: "não " },
      { tipo: "negrito", valor: "é estável" },
      { tipo: "texto", valor: "." },
    ]);
  });
});
