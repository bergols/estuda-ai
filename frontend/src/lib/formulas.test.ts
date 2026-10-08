import katex from "katex";
import { describe, expect, it } from "vitest";

import { separarFormulas } from "./formulas";

const formulas = (texto: string) =>
  separarFormulas(texto)
    .filter((p) => p.tipo === "formula")
    .map((p) => (p.tipo === "formula" ? `${p.bloco ? "B" : "I"}:${p.valor}` : ""));

describe("delimitadores", () => {
  it("inline com $...$", () => {
    expect(formulas("A derivada de $x^2$ é $2x$.")).toEqual(["I:x^2", "I:2x"]);
  });

  it("bloco com $$...$$ e \\[...\\]", () => {
    expect(formulas("Calcule $$\\int_0^1 x^2\\,dx$$ e \\[ \\lim_{x\\to 0} \\frac{\\sin x}{x} \\]")).toEqual([
      "B:\\int_0^1 x^2\\,dx",
      "B:\\lim_{x\\to 0} \\frac{\\sin x}{x}",
    ]);
  });

  it("inline com \\(...\\)", () => {
    expect(formulas("Se \\(f(x)=e^x\\), então...")).toEqual(["I:f(x)=e^x"]);
  });

  it("texto em volta fica intacto", () => {
    expect(separarFormulas("antes $a+b$ depois")).toEqual([
      { tipo: "texto", valor: "antes " },
      { tipo: "formula", valor: "a+b", bloco: false },
      { tipo: "texto", valor: " depois" },
    ]);
  });
});

describe("cifrão que NÃO é fórmula", () => {
  it.each([
    "Custou R$ 10 e depois R$ 20.",
    "Gasto de US$ 0.0348 no mês.",
    "Entre $5 e $10 por mês.",
    "Só um $ solto aqui.",
    "Termina com $",
    "Preço: R$10,00 ou R$15,00",
  ])("%s", (texto) => {
    expect(formulas(texto)).toEqual([]);
    expect(separarFormulas(texto).map((p) => p.valor).join("")).toBe(texto);
  });

  it("\\$ vira cifrão literal", () => {
    expect(separarFormulas("custa \\$5 e \\$10")).toEqual([{ tipo: "texto", valor: "custa $5 e $10" }]);
  });

  it("$$ sem fechamento fica como texto", () => {
    expect(formulas("abre $$ e não fecha")).toEqual([]);
  });
});

describe("segurança: o KaTeX com trust: false não gera link nem HTML", () => {
  it.each(["\\href{javascript:alert(1)}{x}", "\\url{https://exemplo.com}", "\\htmlClass{a}{b}", "<img src=x onerror=alert(1)>"])(
    "%s",
    (formula) => {
      const html = katex.renderToString(formula, { throwOnError: false, trust: false, strict: "ignore" });
      // Tags e atributos de verdade (dentro de <...>); o mesmo texto ESCAPADO (&lt;img ...)
      // pode aparecer na anotação MathML, e isso é inofensivo
      expect(html).not.toMatch(/<a\b|<img\b|<[^>]*\s(href|onerror|src)=/);
      if (formula.startsWith("<")) expect(html).toContain("&lt;img");
    },
  );
});
