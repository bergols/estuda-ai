import { describe, expect, it } from "vitest";

import { agrupar, materialDe } from "./ordem-conteudo";

const item = (topico: string | null, ...materiais: number[]) => ({
  topico,
  trechos: materiais.map((material_id) => ({ material_id })),
});

describe("agrupar por material e tópico", () => {
  const nomes = new Map([[1, "Ordenacao1"], [2, "Ordenacao2"]]);

  it("junta vizinhos e numera na ordem da lista", () => {
    const grupos = agrupar([item("Heap", 2, 1), item("Shell", 1), item("Heap", 2), item("Heap", 2), item(null)], nomes);
    expect(grupos.map((g) => g.material)).toEqual(["Ordenacao1", "Ordenacao2", null]);
    expect(grupos[1].topicos).toHaveLength(1);
    expect(grupos[1].topicos[0].itens.map((i) => i.numero)).toEqual([3, 4]);
    expect(grupos[2].material).toBeNull();
  });

  it("o material de um item é o de menor id entre os trechos citados", () => {
    expect(materialDe(item("x", 5, 2, 7))).toBe(2);
    expect(materialDe(item("x"))).toBeNull();
  });
});
