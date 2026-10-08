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

/** "2026-10-01" (primeiro dia do mês) → "out/26". Meio-dia: longe da virada de fuso. */
export function mes(isoDia: string) {
  const d = new Date(`${isoDia}T12:00`);
  return `${d.toLocaleDateString("pt-BR", { month: "short" }).replace(".", "")}/${String(d.getFullYear()).slice(2)}`;
}

export const usd = (v: string | number) => `US$ ${Number(v).toFixed(4)}`;

/** 3900 → "1 h 05 min"; 1500 → "25 min"; 40 → "40 s" */
export function duracao(segundos: number | null | undefined) {
  if (segundos == null) return "—";
  if (segundos < 60) return `${Math.round(segundos)} s`;
  const minutos = Math.round(segundos / 60);
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  return h > 0 ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}

const HORA = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });
export const hora = (iso: string | number) => HORA.format(new Date(iso));
