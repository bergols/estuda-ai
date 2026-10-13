/**
 * Ênfase mínima no texto dos cards e questões: **negrito**, *itálico* e `código`.
 * Não é markdown completo (sem listas, links, títulos), e não gera HTML: devolve
 * pedaços que o componente desenha como <strong>/<em>/<code> do React, então o texto
 * continua escapado (um "<script>" no material aparece como texto).
 *
 * As fórmulas ($...$) são separadas ANTES (lib/formulas.ts): um "*" dentro de LaTeX
 * nunca chega aqui. Um asterisco solto ("a * b", "2*3") não vira itálico: a marca
 * precisa encostar no texto (sem espaço logo depois de abrir nem antes de fechar).
 */

export type Pedaco = { tipo: "texto" | "negrito" | "italico" | "codigo"; valor: string };

const MARCAS = /(`[^`\n]+`)|(\*\*[^\s*](?:[^*\n]*[^\s*])?\*\*)|(\*[^\s*](?:[^*\n]*[^\s*])?\*)/g;

export function enfatizar(texto: string): Pedaco[] {
  const pedacos: Pedaco[] = [];
  let fim = 0;
  for (const m of texto.matchAll(MARCAS)) {
    const inicio = m.index ?? 0;
    if (inicio > fim) pedacos.push({ tipo: "texto", valor: texto.slice(fim, inicio) });
    const [inteiro, codigo, negrito] = m;
    if (codigo) pedacos.push({ tipo: "codigo", valor: inteiro.slice(1, -1) });
    else if (negrito) pedacos.push({ tipo: "negrito", valor: inteiro.slice(2, -2) });
    else pedacos.push({ tipo: "italico", valor: inteiro.slice(1, -1) });
    fim = inicio + inteiro.length;
  }
  if (fim < texto.length) pedacos.push({ tipo: "texto", valor: texto.slice(fim) });
  return pedacos;
}
