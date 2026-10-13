import { materialDe } from "./ordem-conteudo";

/**
 * Regras do modo baralho (um flashcard por vez). Os cards já chegam na ordem da
 * matéria (servidor); aqui só filtro por material e a posição atual.
 */

type Card = { trechos: { material_id: number }[] };

export type MaterialDoBaralho = { id: number | null; nome: string; quantidade: number };

/** Os materiais presentes no baralho, na ordem em que aparecem, com quantos cards cada. */
export function materiaisDoBaralho(cards: Card[], nomes: Map<number, string>): MaterialDoBaralho[] {
  const lista: MaterialDoBaralho[] = [];
  for (const c of cards) {
    const id = materialDe(c);
    const existente = lista.find((m) => m.id === id);
    if (existente) existente.quantidade++;
    else lista.push({ id, nome: id === null ? "Sem material" : (nomes.get(id) ?? `Material ${id}`), quantidade: 1 });
  }
  return lista;
}

/** `"todos"` = o baralho inteiro; senão, só os cards daquele material (`null` = sem material). */
export function filtrar<T extends Card>(cards: T[], material: number | null | "todos"): T[] {
  return material === "todos" ? cards : cards.filter((c) => materialDe(c) === material);
}

/** Posição sempre dentro do baralho (inclusive quando o filtro diminui o total). */
export function limitar(indice: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(Math.max(indice, 0), total - 1);
}
