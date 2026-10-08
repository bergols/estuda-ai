"use client";

import { useSyncExternalStore } from "react";

type Tema = "sistema" | "claro" | "escuro";
const PROXIMO: Record<Tema, Tema> = { sistema: "claro", claro: "escuro", escuro: "sistema" };

const ouvintes = new Set<() => void>();

function ler(): Tema {
  const t = document.documentElement.dataset.tema;
  return t === "claro" || t === "escuro" ? t : "sistema";
}

function mudar(tema: Tema) {
  if (tema === "sistema") delete document.documentElement.dataset.tema;
  else document.documentElement.dataset.tema = tema;
  try {
    if (tema === "sistema") localStorage.removeItem("tema");
    else localStorage.setItem("tema", tema);
  } catch {
    // modo privado sem localStorage: o tema vale só nesta página
  }
  ouvintes.forEach((avisar) => avisar());
}

/** Alterna sistema → claro → escuro. O padrão segue o sistema (prefers-color-scheme). */
export function BotaoTema() {
  const tema = useSyncExternalStore(
    (avisar) => {
      ouvintes.add(avisar);
      return () => ouvintes.delete(avisar);
    },
    ler,
    () => "sistema" as Tema, // no servidor não há localStorage
  );
  return (
    <button
      type="button"
      onClick={() => mudar(PROXIMO[tema])}
      className="text-sm text-apagado hover:text-acento"
      title="Alternar tema"
    >
      tema: {tema}
    </button>
  );
}
