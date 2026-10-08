"use client";

import { useCallback, useEffect, useState } from "react";

import { useSpotify } from "@/lib/consultas";
import { type Momento, acaoMusical } from "@/lib/foco/musica";
import { type Tocando, atual, mensagemDoErro, pausar, retomar, tocar } from "@/lib/foco/spotify";
import type { Metodo } from "@/lib/foco/timer";

/**
 * Executa a ação musical de cada momento da sessão (lib/foco/musica.ts decide; aqui
 * só chama o Spotify) e guarda o aviso de erro para a tela mostrar. Um erro de música
 * nunca atrapalha a sessão: ela segue, e a tela diz o que fazer ("abra o Spotify").
 */
export function useMusica() {
  const spotify = useSpotify();
  const config = spotify.data;
  const [aviso, setAviso] = useState<string | null>(null);

  const executar = useCallback(
    (momento: Momento, sessao: { metodo: Metodo; disciplinaId: number | null }) => {
      if (!config) return;
      const acao = acaoMusical(momento, config, sessao);
      const chamada =
        acao.tipo === "tocar" ? tocar(acao.uri)
        : acao.tipo === "pausar" ? pausar()
        : acao.tipo === "retomar" ? retomar()
        : null;
      chamada?.then(
        () => setAviso(null),
        (erro) => setAviso(mensagemDoErro(erro)),
      );
    },
    [config],
  );

  return { executar, aviso, conectado: !!config?.conectado };
}

/** A música atual, consultada a cada 10 s enquanto a sessão está ativa. */
export function useMusicaAtual(ativa: boolean, conectado: boolean): Tocando | null {
  const [tocando, setTocando] = useState<Tocando | null>(null);
  useEffect(() => {
    if (!ativa || !conectado) return;
    let cancelado = false;
    const consultar = () => {
      atual().then(
        (t) => !cancelado && setTocando(t),
        () => {}, // limite, Spotify fechado...: o aviso vem das ações; aqui só não atualiza
      );
    };
    consultar();
    const id = setInterval(consultar, 10_000);
    return () => {
      cancelado = true;
      clearInterval(id);
    };
  }, [ativa, conectado]);
  return ativa ? tocando : null;
}
