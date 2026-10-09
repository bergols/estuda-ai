"use client";

import { useEffect, useRef, useState } from "react";

import type { Tocando } from "@/lib/foco/spotify";

export type ControlesPlayer = {
  tocando: Tocando | null;
  alternar: () => void;
  proxima: () => void;
  anterior: () => void;
  mudarVolume: (v: number) => void;
};

function Icone({ d, rotulo }: { d: string; rotulo: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" role="img" aria-label={rotulo}>
      <path d={d} />
    </svg>
  );
}

const ICONES = {
  anterior: "M6 6h2v12H6zM9.5 12 18 6v12z",
  proxima: "M16 6h2v12h-2zM6 18V6l8.5 6z",
  tocar: "M8 5v14l11-7z",
  pausar: "M7 5h4v14H7zM13 5h4v14h-4z",
};

const BOTAO =
  "inline-flex size-10 items-center justify-center rounded-full text-tinta hover:text-acento focus-visible:outline-2 focus-visible:outline-acento";

/**
 * O player do Spotify durante a sessão: capa, música, anterior/tocar/próxima e volume.
 * O volume é enviado com um pequeno atraso (debounce): arrastar o controle gera dezenas
 * de eventos, e cada um seria uma chamada ao Spotify (o limite de taxa chegaria rápido).
 */
export function PlayerMusica({ player }: { player: ControlesPlayer }) {
  const t = player.tocando;
  const [volume, setVolume] = useState<number | null>(null);
  const espera = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (espera.current) clearTimeout(espera.current);
  }, []);

  if (!t) return null;
  const volumeMostrado = volume ?? t.volume;

  function arrastar(v: number) {
    setVolume(v);
    if (espera.current) clearTimeout(espera.current);
    espera.current = setTimeout(() => {
      player.mudarVolume(v);
      setVolume(null);
    }, 350);
  }

  return (
    <section aria-label="Música" className="flex w-full max-w-xl items-center gap-4 border-y border-fio py-3 text-left">
      {t.capa ? (
        // eslint-disable-next-line @next/next/no-img-element -- imagem externa do Spotify, sem otimização do Next
        <img src={t.capa} alt="" className="size-16 shrink-0 rounded-sm object-cover" />
      ) : (
        <div aria-hidden className="size-16 shrink-0 rounded-sm bg-tarja" />
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-base">{t.musica}</p>
        <p className="truncate text-sm text-apagado">{t.artistas}</p>
        <div className="mt-1 flex items-center gap-1">
          <button type="button" className={BOTAO} onClick={player.anterior}>
            <Icone d={ICONES.anterior} rotulo="Música anterior" />
          </button>
          <button type="button" className={BOTAO} onClick={player.alternar}>
            <Icone d={t.tocando ? ICONES.pausar : ICONES.tocar} rotulo={t.tocando ? "Pausar a música" : "Tocar"} />
          </button>
          <button type="button" className={BOTAO} onClick={player.proxima}>
            <Icone d={ICONES.proxima} rotulo="Próxima música" />
          </button>
          {volumeMostrado != null && (
            <label className="ml-3 flex flex-1 items-center gap-2 text-xs text-apagado">
              <span className="sr-only">Volume</span>
              <span aria-hidden>vol</span>
              <input type="range" min={0} max={100} step={1} value={volumeMostrado}
                onChange={(e) => arrastar(Number(e.target.value))}
                className="w-full accent-acento" />
              <span className="w-8 text-right tabular-nums">{volumeMostrado}</span>
            </label>
          )}
        </div>
      </div>
    </section>
  );
}
