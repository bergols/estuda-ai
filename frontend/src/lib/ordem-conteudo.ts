/**
 * Agrupa cards/questões (já na ordem da matéria, que vem do servidor) por material e,
 * dentro dele, por tópico. Só junta itens VIZINHOS: a ordem da lista é a do servidor
 * e não muda aqui (um tópico que reaparece mais adiante vira outro grupo).
 */

type Item = { topico?: string | null; trechos: { material_id: number }[] };

export type Grupo<T> = {
  material: string | null;
  topicos: { topico: string | null; itens: { item: T; numero: number }[] }[];
};

/** O material de um item: o primeiro (menor id) entre os trechos que ele cita. */
export function materialDe(item: Item): number | null {
  return item.trechos.length ? Math.min(...item.trechos.map((t) => t.material_id)) : null;
}

export function agrupar<T extends Item>(itens: T[], nomes: Map<number, string>): Grupo<T>[] {
  const grupos: Grupo<T>[] = [];
  let materialAtual: number | null | undefined;
  itens.forEach((item, i) => {
    const material = materialDe(item);
    if (grupos.length === 0 || material !== materialAtual) {
      grupos.push({ material: material === null ? null : (nomes.get(material) ?? `Material ${material}`), topicos: [] });
      materialAtual = material;
    }
    const grupo = grupos[grupos.length - 1];
    const topico = item.topico ?? null;
    const ultimo = grupo.topicos[grupo.topicos.length - 1];
    if (!ultimo || ultimo.topico !== topico) grupo.topicos.push({ topico, itens: [] });
    grupo.topicos[grupo.topicos.length - 1].itens.push({ item, numero: i + 1 });
  });
  return grupos;
}
