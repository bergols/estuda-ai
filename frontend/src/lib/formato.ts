/** Formatação para pt-BR. Datas da API vêm em ISO com fuso (timestamptz). */

const DATA = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
const DATA_CURTA = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "short" });

export const data = (iso: string) => DATA.format(new Date(iso));
export const dataCurta = (iso: string) => DATA_CURTA.format(new Date(iso));

/** "2026-10-08" (dia local, sem hora) → sem converter de fuso, senão vira o dia anterior. */
export function dia(isoDia: string) {
  const [a, m, d] = isoDia.split("-").map(Number);
  return DATA_CURTA.format(new Date(a, m - 1, d));
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export const porcento = (taxa: number | null | undefined) =>
  taxa == null ? "—" : `${Math.round(taxa * 100)}%`;

export function paginas(inicio: number | null | undefined, fim: number | null | undefined) {
  if (inicio == null) return null;
  return fim && fim !== inicio ? `p. ${inicio}–${fim}` : `p. ${inicio}`;
}
