"use client";

import { useCallback, useEffect, useState } from "react";

import { useSpotify } from "@/lib/consultas";
import { type Momento, acaoMusical } from "@/lib/foco/musica";
import {
  type Tocando,
  anterior,
  atual,
  mensagemDoErro,
  pausar,
  proxima,
  retomar,
  tocar,
  volume,
} from "@/lib/foco/spotify";
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
    (momento: Momento, sessao: { metodo: Metodo; disciplinaId: number | null }, depois?: () => void) => {
      if (!config) return;
      const acao = acaoMusical(momento, config, sessao);
      const chamada =
        acao.tipo === "tocar" ? tocar(acao.uri)
        : acao.tipo === "pausar" ? pausar()
        : acao.tipo === "retomar" ? retomar()
        : null;
      chamada?.then(
        () => {
          setAviso(null);
          depois?.();
        },
        (erro) => setAviso(mensagemDoErro(erro)),
      );
    },
    [config],
  );

  return { executar, aviso, setAviso, conectado: !!config?.conectado };
}

/**
 * A música atual e os controles do player. Consulta a cada 5 s enquanto a sessão está
 * ativa e LOGO DEPOIS de cada comando (com um pequeno atraso: o Spotify leva algumas
 * centenas de ms para refletir o play/pause). Sem isso, a primeira consulta, feita no
 * mesmo instante do play, mostrava "pausada" por 10 s.
 */
export function usePlayer(ativa: boolean, conectado: boolean, aoErrar: (mensagem: string) => void) {
  const [tocando, setTocando] = useState<Tocando | null>(null);
  const [versao, setVersao] = useState(0); // incrementar = consultar de novo

  useEffect(() => {
    if (!ativa || !conectado) return;
    let cancelado = false;
    const consultar = () => {
      atual().then(
        (t) => !cancelado && setTocando(t),
        () => {}, // limite, Spotify fechado...: o aviso vem dos comandos; aqui só não atualiza
      );
    };
    const primeira = setTimeout(consultar, versao === 0 ? 1500 : 700);
    const id = setInterval(consultar, 5_000);
    return () => {
      cancelado = true;
      clearTimeout(primeira);
      clearInterval(id);
    };
  }, [ativa, conectado, versao]);

  const executar = useCallback(
    (comando: () => Promise<void>, otimista?: (t: Tocando) => Tocando) => {
      if (otimista) setTocando((t) => (t ? otimista(t) : t));
      comando().then(
        () => setVersao((v) => v + 1),
        (erro) => {
          aoErrar(mensagemDoErro(erro));
          setVersao((v) => v + 1);
        },
      );
    },
    [aoErrar],
  );

  return {
    tocando: ativa ? tocando : null,
    alternar: () =>
      executar(tocando?.tocando ? pausar : retomar, (t) => ({ ...t, tocando: !t.tocando })),
    proxima: () => executar(proxima),
    anterior: () => executar(anterior),
    mudarVolume: (v: number) => executar(() => volume(v), (t) => ({ ...t, volume: v })),
    atualizar: () => setVersao((v) => v + 1),
  };
}
