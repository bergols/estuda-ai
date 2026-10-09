"use client";

import { useCallback, useEffect, useState } from "react";

import { useSpotify } from "@/lib/consultas";
import { CONSULTA, type Momento, acaoMusical, proximaConsulta } from "@/lib/foco/musica";
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
 * A música atual e os controles do player. Consulta enquanto a sessão está ativa:
 *  - LOGO DEPOIS de cada comando (com um pequeno atraso: o Spotify leva algumas centenas
 *    de ms para refletir o play/pause). Sem isso, a primeira consulta, feita no mesmo
 *    instante do play, mostrava "pausada";
 *  - quando a música atual deve acabar (proximaConsulta, em lib/foco/musica.ts), e não
 *    a cada 5 s: o limite de chamadas do Spotify no modo de desenvolvimento é baixo;
 *  - quando a janela volta a aparecer ou ganha o foco (a pessoa pode ter trocado a
 *    música pelo próprio Spotify). Com a janela escondida (minimizada), não consulta.
 */
export function usePlayer(ativa: boolean, conectado: boolean, aoErrar: (mensagem: string) => void) {
  const [tocando, setTocando] = useState<Tocando | null>(null);
  const [versao, setVersao] = useState(0); // incrementar = consultar de novo

  useEffect(() => {
    if (!ativa || !conectado) return;
    let cancelado = false;
    // Um só timer: cada consulta agenda a seguinte (setTimeout encadeado, e não setInterval,
    // porque o intervalo depende da resposta)
    let timer: ReturnType<typeof setTimeout> | undefined;
    const agendar = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(consultar, ms);
    };
    const consultar = () => {
      if (document.hidden) return; // volta a consultar no visibilitychange
      atual().then(
        (t) => {
          if (cancelado) return;
          setTocando(t);
          agendar(proximaConsulta(t));
        },
        // Limite, Spotify fechado...: o aviso vem dos comandos; aqui só tenta mais tarde
        () => !cancelado && agendar(CONSULTA.parado),
      );
    };
    const aoVoltar = () => {
      if (!document.hidden) agendar(300);
    };
    agendar(versao === 0 ? 1500 : 700);
    document.addEventListener("visibilitychange", aoVoltar);
    window.addEventListener("focus", aoVoltar);
    return () => {
      cancelado = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", aoVoltar);
      window.removeEventListener("focus", aoVoltar);
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
