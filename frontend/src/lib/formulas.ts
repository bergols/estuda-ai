/**
 * Separa um texto em pedaços de texto comum e de fórmula (LaTeX), para desenhar as
 * fórmulas com o KaTeX e deixar o resto como texto escapado pelo React.
 *
 * Delimitadores:
 *   $$ ... $$  e  \[ ... \]   fórmula em destaque (bloco)
 *   $ ... $    e  \( ... \)   fórmula no meio da frase
 *   \$                         um cifrão literal
 *
 * O "$" sozinho é ambíguo em português: "R$ 10", "US$ 5". Regras (as do pandoc, mais
 * uma): o "$" que abre não pode vir colado depois de letra ou número (R$, US$) nem ter
 * espaço logo depois; o que fecha não pode ter espaço logo antes nem número logo depois.
 * Sem um par válido, o "$" fica como texto.
 */

export type Pedaco =
  | { tipo: "texto"; valor: string }
  | { tipo: "formula"; valor: string; bloco: boolean };

const ESPACO = /\s/;
const ALFANUMERICO = /[\p{L}\p{N}]/u;
const DIGITO = /\d/;

function fechamentoInline(texto: string, inicio: number): number {
  // inicio = posição logo depois do "$" de abertura
  for (let j = inicio + 1; j < texto.length; j++) {
    if (texto[j] === "\\") {
      j++; // pula o caractere escapado (\$ dentro da fórmula)
      continue;
    }
    if (texto[j] !== "$") continue;
    if (!ESPACO.test(texto[j - 1]) && !DIGITO.test(texto[j + 1] ?? "")) return j;
  }
  return -1;
}

export function separarFormulas(texto: string): Pedaco[] {
  const pedacos: Pedaco[] = [];
  let buffer = "";
  const empurrarTexto = () => {
    if (buffer) pedacos.push({ tipo: "texto", valor: buffer });
    buffer = "";
  };
  const empurrarFormula = (valor: string, bloco: boolean) => {
    empurrarTexto();
    pedacos.push({ tipo: "formula", valor: valor.trim(), bloco });
  };

  let i = 0;
  while (i < texto.length) {
    const c = texto[i];
    const prox = texto[i + 1];

    if (c === "\\" && prox === "$") {
      buffer += "$";
      i += 2;
      continue;
    }
    if (c === "\\" && (prox === "[" || prox === "(")) {
      const fecha = prox === "[" ? "\\]" : "\\)";
      const fim = texto.indexOf(fecha, i + 2);
      if (fim !== -1) {
        empurrarFormula(texto.slice(i + 2, fim), prox === "[");
        i = fim + 2;
        continue;
      }
    }
    if (c === "$" && prox === "$") {
      const fim = texto.indexOf("$$", i + 2);
      if (fim !== -1 && texto.slice(i + 2, fim).trim()) {
        empurrarFormula(texto.slice(i + 2, fim), true);
        i = fim + 2;
        continue;
      }
    }
    if (c === "$" && prox !== "$") {
      const anterior = texto[i - 1] ?? "";
      const abreValido = prox !== undefined && !ESPACO.test(prox) && !ALFANUMERICO.test(anterior);
      const fim = abreValido ? fechamentoInline(texto, i) : -1;
      if (fim !== -1) {
        empurrarFormula(texto.slice(i + 1, fim), false);
        i = fim + 1;
        continue;
      }
    }
    buffer += c;
    i++;
  }
  empurrarTexto();
  return pedacos;
}

/** Atalho: o texto tem alguma fórmula? (evita carregar o KaTeX à toa) */
export function temFormula(texto: string): boolean {
  return separarFormulas(texto).some((p) => p.tipo === "formula");
}
