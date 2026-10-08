"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { chaves } from "@/lib/consultas";
import { escutarSincronia, salvarSessao, situacaoSincronia, type SituacaoSincronia } from "@/lib/foco/local";
import {
  bater,
  encerrar,
  precisaBatida,
  registrarPausasPlanejadas,
  configDe,
  type SessaoAtiva,
} from "@/lib/foco/sessao";
import { momento, plano } from "@/lib/foco/timer";

export const novaChave = () => crypto.randomUUID();

/**
 * Estado da sessão em andamento + gravação no SQLite a cada mudança.
 *
 * `mudar(f)` aplica uma função PURA de lib/foco/sessao.ts e grava o resultado. A
 * referência (useRef) guarda a versão atual para que duas mudanças seguidas (um tick e
 * um clique no mesmo instante) não se sobrescrevam.
 */
export function useSessao(inicial: SessaoAtiva | null) {
  const ref = useRef<SessaoAtiva | null>(inicial);
  const [sessao, setSessao] = useState<SessaoAtiva | null>(inicial);
  const [erroAoGravar, setErroAoGravar] = useState<string | null>(null);

  const mudar = useCallback((f: (s: SessaoAtiva) => SessaoAtiva | null) => {
    const atual = ref.current;
    if (!atual) return;
    const nova = f(atual);
    if (nova === atual) return;
    ref.current = nova;
    setSessao(nova);
    if (nova) {
      salvarSessao(nova).then(
        () => setErroAoGravar(null),
        (e) => setErroAoGravar(String(e)),
      );
    }
  }, []);

  const comecar = useCallback((s: SessaoAtiva | null) => {
    ref.current = s;
    setSessao(s);
    if (s) salvarSessao(s).catch((e) => setErroAoGravar(String(e)));
  }, []);

  return { sessao, mudar, comecar, erroAoGravar };
}

/**
 * O "relógio" da tela: a cada 250 ms registra as pausas planejadas que terminaram,
 * grava a batida de vida e encerra sozinho quando o plano acaba. O desenho do timer
 * é sempre calculado do relógio (lib/foco/timer.ts), então um tick atrasado não erra
 * o tempo, só atualiza a tela um pouco depois.
 */
export function useRelogio(
  sessao: SessaoAtiva | null,
  mudar: (f: (s: SessaoAtiva) => SessaoAtiva | null) => void,
  aoMudarDeFase: (indice: number) => void,
) {
  const [agora, setAgora] = useState(() => Date.now());
  const ativa = sessao?.dados.status === "em_andamento";
  const faseAnterior = useRef<number | null>(null);

  useEffect(() => {
    if (!ativa) return;
    const tick = () => {
      const t = Date.now();
      setAgora(t);
      mudar((s) => {
        if (s.dados.status !== "em_andamento") return s;
        const m = momento(plano(configDe(s.dados)), s.local.timer, t);
        if (faseAnterior.current !== null && faseAnterior.current !== m.indice) aoMudarDeFase(m.indice);
        faseAnterior.current = m.indice;
        if (m.terminado) return encerrar(s, t, novaChave);
        const comPausas = registrarPausasPlanejadas(s, t, novaChave);
        return precisaBatida(comPausas, t) ? bater(comPausas, t) : comPausas;
      });
    };
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [ativa, mudar, aoMudarDeFase]);

  return agora;
}

/** Quantas sessões esperam envio (o laço do Rust avisa depois de cada tentativa). */
export function useSituacaoSincronia() {
  const [situacao, setSituacao] = useState<SituacaoSincronia | null>(null);
  const cliente = useQueryClient();
  useEffect(() => {
    let cancelado = false;
    situacaoSincronia().then((s) => !cancelado && setSituacao(s), () => {});
    const parar = escutarSincronia((s) => {
      setSituacao(s);
      // Tudo enviado: o histórico do servidor já tem as sessões novas
      if (s.pendentes === 0) cliente.invalidateQueries({ queryKey: chaves.sessoes });
    });
    return () => {
      cancelado = true;
      parar.then((f) => f());
    };
  }, [cliente]);
  return situacao;
}
