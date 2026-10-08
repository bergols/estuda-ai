"use client";

import katex from "katex";
import { useMemo } from "react";

import { separarFormulas } from "@/lib/formulas";

/**
 * Texto com fórmulas LaTeX desenhadas pelo KaTeX ($x^2$, $$\int f$$; regras em
 * lib/formulas.ts). O texto comum continua sendo escapado pelo React.
 *
 * Segurança: a saída do KaTeX entra com dangerouslySetInnerHTML, então a configuração
 * importa. trust: false desliga \href, \url, \includegraphics e \html* (um material ou
 * resposta maliciosa não injeta link nem HTML); o KaTeX escapa todo o resto
 * (lib/formulas.test.ts confere). throwOnError: false mostra uma fórmula inválida em
 * vermelho, com o código, em vez de quebrar a tela.
 */
const OPCOES = { throwOnError: false, trust: false, strict: "ignore" as const, output: "htmlAndMathml" as const };

export function TextoRico({ children, className }: { children: string; className?: string }) {
  const pedacos = useMemo(() => separarFormulas(children), [children]);
  return (
    <span className={className}>
      {pedacos.map((p, i) =>
        p.tipo === "texto" ? (
          p.valor
        ) : (
          <span
            key={i}
            className={p.bloco ? "my-2 block overflow-x-auto overflow-y-hidden" : undefined}
            dangerouslySetInnerHTML={{ __html: katex.renderToString(p.valor, { ...OPCOES, displayMode: p.bloco }) }}
          />
        ),
      )}
    </span>
  );
}
