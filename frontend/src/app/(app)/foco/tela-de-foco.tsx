"use client";

import { useState } from "react";

import { Botao } from "@/components/ui";
import type { SituacaoSincronia } from "@/lib/foco/local";
import { configDe, type SessaoAtiva } from "@/lib/foco/sessao";
import { focoPlanejadoMs, momento, plano, relogio } from "@/lib/foco/timer";

import { RodapeSincronia } from "./rodape-sincronia";

const NOME_FASE = { foco: "Foco", pausa_curta: "Pausa", pausa_longa: "Pausa longa" } as const;

/**
 * A tela durante a sessão: cobre a janela inteira (o Rust a põe em tela cheia e por
 * cima das outras). Só o essencial: o tempo, a fase, a meta. Encerrar pede uma
 * segunda confirmação para não acabar a sessão com um clique acidental.
 */
export function TelaDeFoco({
  sessao,
  agora,
  disciplina,
  sincronia,
  aoPausar,
  aoRetomar,
  aoEncerrar,
}: {
  sessao: SessaoAtiva;
  agora: number;
  disciplina: string | null;
  sincronia: SituacaoSincronia | null;
  aoPausar: () => void;
  aoRetomar: () => void;
  aoEncerrar: () => void;
}) {
  const [confirmando, setConfirmando] = useState(false);
  const config = configDe(sessao.dados);
  const fases = plano(config);
  const m = momento(fases, sessao.local.timer, agora);
  const fase = m.fase;
  const emFoco = fase?.tipo === "foco";
  const progresso = fase ? 1 - m.restanteMs / fase.duracaoMs : 1;
  const ciclos = config.ciclos;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-papel text-tinta">
      {/* Progresso da fase: um fio que avança no topo */}
      <div className="h-0.5 w-full bg-fio" aria-hidden>
        <div className="h-full bg-acento transition-[width] duration-300" style={{ width: `${progresso * 100}%` }} />
      </div>

      <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 text-center">
        <p className="rotulo" aria-live="polite">
          {m.pausado ? "Pausado" : fase ? NOME_FASE[fase.tipo] : "Fim"}
          {fase && ciclos > 1 && ` · ciclo ${fase.ciclo} de ${ciclos}`}
        </p>
        <p
          className={`font-mono text-[clamp(4.5rem,20vw,13rem)] leading-none tabular-nums ${
            m.pausado ? "text-apagado" : emFoco ? "text-tinta" : "text-acento"
          }`}
          role="timer"
          aria-label={`${relogio(m.restanteMs)} restantes`}
        >
          {relogio(m.restanteMs)}
        </p>
        {sessao.dados.meta && <p className="max-w-xl font-serif text-2xl italic">{sessao.dados.meta}</p>}
        <p className="text-sm text-apagado">
          {disciplina ?? "Sem disciplina"} · {relogio(m.focoCumpridoMs)} de foco de {relogio(focoPlanejadoMs(config))}
        </p>
      </main>

      <footer className="flex flex-wrap items-center justify-center gap-4 border-t border-fio px-4 py-4">
        {m.pausado ? (
          <Botao onClick={aoRetomar}>Retomar</Botao>
        ) : (
          <Botao variante="secundario" onClick={aoPausar} disabled={!emFoco}
            title={emFoco ? undefined : "Durante a pausa planejada o tempo já está parado para você"}>
            Pausar
          </Botao>
        )}
        {confirmando ? (
          <span className="flex items-center gap-3 text-sm">
            Encerrar agora?
            <Botao variante="secundario" onClick={aoEncerrar}>Sim, encerrar</Botao>
            <Botao variante="discreto" onClick={() => setConfirmando(false)}>continuar estudando</Botao>
          </span>
        ) : (
          <Botao variante="discreto" onClick={() => setConfirmando(true)}>Encerrar sessão</Botao>
        )}
        <RodapeSincronia situacao={sincronia} className="w-full text-center sm:absolute sm:right-4 sm:w-auto" />
      </footer>
    </div>
  );
}
