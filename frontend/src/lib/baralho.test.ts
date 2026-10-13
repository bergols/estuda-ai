import { describe, expect, it } from "vitest";

import { filtrar, limitar, materiaisDoBaralho } from "./baralho";

const card = (...materiais: number[]) => ({ trechos: materiais.map((material_id) => ({ material_id })) });
const nomes = new Map([[1, "Ordenacao1"], [4, "IntroGrafos"]]);

describe("baralho", () => {
  const cards = [card(1), card(1, 4), card(4), card()];

  it("lista os materiais na ordem em que aparecem, com a contagem", () => {
    expect(materiaisDoBaralho(cards, nomes)).toEqual([
      { id: 1, nome: "Ordenacao1", quantidade: 2 },
      { id: 4, nome: "IntroGrafos", quantidade: 1 },
      { id: null, nome: "Sem material", quantidade: 1 },
    ]);
  });

  it("filtra por material (o de menor id entre os trechos)", () => {
    expect(filtrar(cards, "todos")).toHaveLength(4);
    expect(filtrar(cards, 1)).toHaveLength(2);
    expect(filtrar(cards, 4)).toHaveLength(1);
    expect(filtrar(cards, null)).toHaveLength(1);
  });

  it("a posição nunca sai do baralho", () => {
    expect(limitar(-1, 10)).toBe(0);
    expect(limitar(10, 10)).toBe(9);
    expect(limitar(5, 3)).toBe(2);
    expect(limitar(3, 0)).toBe(0);
  });
});
