/**
 * Em qual alvo este código foi compilado: "web" (Vercel, com o BFF) ou "desktop"
 * (exportação estática dentro do app Tauri). Fixado no build pelo next.config.ts; como
 * é uma constante, o código do outro alvo é eliminado do pacote final.
 */
export const DESKTOP = process.env.NEXT_PUBLIC_ALVO === "desktop";
