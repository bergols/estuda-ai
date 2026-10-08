"use client";

import { useState } from "react";

import { ChaveLinha, Colunas, Figura, Tabela } from "@/components/graficos";
import { Carregando, Erro, Vazio } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import { useAcertoPosSessao, useFocoHoras, useFocoInterrupcoes, useFocoSessoes } from "@/lib/consultas";
import { dia, duracao, hora, porcento } from "@/lib/formato";

/**
 * Gráficos das sessões de estudo (fase 7). Mesmas regras dos outros do painel
 * (components/graficos.tsx): forma pelo trabalho do dado, um eixo, as duas cores
 * validadas (destaque/contexto), legenda + rótulo direto e tabela em toda figura.
 *
 * Duas figuras são barras em HTML, não Recharts: com 4 ou 5 linhas e o número escrito
 * ao lado, a barra é um apoio visual da tabela, e o texto continua legível sem cor.
 */

const DESTAQUE = "var(--grafico-destaque)";
const CONTEXTO = "var(--grafico-contexto)";

const NOME: Record<string, string> = {
  pomodoro: "Pomodoro",
  bloco: "Bloco contínuo",
  "52_17": "52/17",
  personalizado: "Personalizado",
  todos: "Todos",
  sem_sessao: "Sem sessão antes",
};

/** Abaixo disso, a taxa de um grupo oscila demais para comparar (a tela avisa). */
const POUCAS_RESPOSTAS = 20;

export function SecaoFoco({ disciplinaId }: { disciplinaId?: number }) {
  return (
    <section className="mt-4">
      <h2 className="mb-6 border-b border-tinta pb-2 font-serif text-2xl font-semibold">Sessões de estudo</h2>
      <HorasDeFoco disciplinaId={disciplinaId} />
      <ConclusaoPorMetodo disciplinaId={disciplinaId} />
      <Interrupcoes disciplinaId={disciplinaId} />
      <AcertoAposMetodo disciplinaId={disciplinaId} />
    </section>
  );
}

// ------------------------------------------------------------- horas de foco

function HorasDeFoco({ disciplinaId }: { disciplinaId?: number }) {
  const [agrupar, setAgrupar] = useState<"dia" | "semana">("dia");
  const q = useFocoHoras(agrupar, disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const porDia = agrupar === "dia";
  const pontos = q.data.dados.map((d) => ({
    periodo: d.periodo,
    valor: porDia ? Math.round(d.foco_s / 60) : Math.round((d.foco_s / 3600) * 10) / 10,
  }));
  const rotulo = (p: string) => (porDia ? dia(p) : `sem. ${dia(p)}`);
  const total = q.data.dados.reduce((t, d) => t + d.foco_s, 0);

  return (
    <Figura
      titulo="Foco efetivo"
      sobre={`${porDia ? "minutos por dia" : "horas por semana"} · ${duracao(total)} no período`}
      esmaecida={q.isPlaceholderData}
      legenda={
        <span role="radiogroup" aria-label="Agrupar" className="flex gap-3 text-xs">
          {(["dia", "semana"] as const).map((a) => (
            <button key={a} type="button" role="radio" aria-checked={agrupar === a} onClick={() => setAgrupar(a)}
              className={agrupar === a ? "text-tinta underline decoration-acento underline-offset-4" : "text-apagado hover:text-tinta"}>
              por {a}
            </button>
          ))}
        </span>
      }
      tabela={
        <Tabela
          cabecalho={[porDia ? "Dia" : "Semana de", "Foco efetivo", "Sessões", "Concluídas"]}
          linhas={q.data.dados.map((d) => [dia(d.periodo), duracao(d.foco_s), d.sessoes, d.concluidas])}
        />
      }
    >
      {total === 0 ? (
        <Vazio>Nenhuma sessão terminada no período.</Vazio>
      ) : (
        <Colunas dados={pontos} x="periodo" y="valor" nome={porDia ? "min" : "h"} formatoX={rotulo} />
      )}
    </Figura>
  );
}

// -------------------------------------------------- concluídas × abandonadas

function ConclusaoPorMetodo({ disciplinaId }: { disciplinaId?: number }) {
  const q = useFocoSessoes(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const metodos = q.data.dados.filter((d) => d.metodo !== "todos");
  const todos = q.data.dados.find((d) => d.metodo === "todos");
  if (!todos || todos.sessoes === 0) return null;
  const maximo = Math.max(...metodos.map((d) => d.sessoes), 1);

  return (
    <Figura
      titulo="Sessões concluídas e abandonadas"
      sobre={`por método · ${todos.concluidas} de ${todos.sessoes} concluídas (${porcento(Number(todos.taxa_conclusao))})`}
      esmaecida={q.isPlaceholderData}
      legenda={
        <span className="flex gap-4">
          <ChaveLinha cor={DESTAQUE}>concluídas</ChaveLinha>
          <ChaveLinha cor={CONTEXTO}>abandonadas</ChaveLinha>
        </span>
      }
      tabela={
        <Tabela
          cabecalho={["Método", "Sessões", "Concluídas", "Abandonadas", "Conclusão", "Foco médio"]}
          linhas={q.data.dados.map((d) => [
            NOME[d.metodo], d.sessoes, d.concluidas, d.abandonadas,
            porcento(d.taxa_conclusao == null ? null : Number(d.taxa_conclusao)), duracao(d.foco_medio_s),
          ])}
        />
      }
    >
      <ul className="space-y-3">
        {metodos.map((d) => (
          <li key={d.metodo} className="grid grid-cols-[7rem_1fr] items-center gap-3 text-sm sm:grid-cols-[9rem_1fr_9rem]">
            <span>{NOME[d.metodo]}</span>
            {/* Duas fatias na MESMA barra, com 2px de papel entre elas */}
            <span className="flex h-3 gap-0.5" aria-hidden>
              <span className="rounded-l-sm" style={{ width: `${(d.concluidas / maximo) * 100}%`, background: DESTAQUE }} />
              <span className="rounded-r-sm" style={{ width: `${(d.abandonadas / maximo) * 100}%`, background: CONTEXTO }} />
            </span>
            <span className="col-span-2 text-xs text-apagado tabular-nums sm:col-span-1 sm:text-right">
              {d.sessoes === 0 ? "não usado" : `${d.concluidas} de ${d.sessoes} concluídas`}
            </span>
          </li>
        ))}
      </ul>
    </Figura>
  );
}

// -------------------------------------------------------------- interrupções

type Interrupcao = Esquemas["InterrupcoesSessao"];

function Interrupcoes({ disciplinaId }: { disciplinaId?: number }) {
  const q = useFocoInterrupcoes(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  if (q.data.dados.length === 0) return null;
  const rotulo = (s: Interrupcao) => `${dia(s.dia)} ${hora(s.iniciada_em)}`;
  const pontos = q.data.dados.map((s) => ({ sessao: rotulo(s), interrupcoes: s.interrupcoes }));
  const media = q.data.dados.reduce((t, s) => t + s.interrupcoes, 0) / q.data.dados.length;

  return (
    <Figura
      titulo="Interrupções por sessão"
      sobre={`saídas da janela e tentativas bloqueadas · média ${media.toFixed(1)}`}
      esmaecida={q.isPlaceholderData}
      tabela={
        <Tabela
          cabecalho={["Sessão", "Método", "Interrupções", "Tempo fora", "Por hora de foco"]}
          linhas={q.data.dados.map((s) => [
            rotulo(s), NOME[s.metodo], s.interrupcoes, duracao(s.fora_s), s.por_hora == null ? "—" : Number(s.por_hora).toFixed(1),
          ])}
        />
      }
    >
      <Colunas dados={pontos} x="sessao" y="interrupcoes" nome="interrupções" formatoX={(v) => v} />
    </Figura>
  );
}

// ------------------------------------------------- acerto logo depois do método

function AcertoAposMetodo({ disciplinaId }: { disciplinaId?: number }) {
  const q = useAcertoPosSessao(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  if (!q.data.dados.some((d) => d.grupo !== "sem_sessao" && d.respostas > 0)) return null;

  return (
    <Figura
      titulo="Acerto logo depois de cada método"
      sobre="cards e questões respondidos até 60 min depois do fim da sessão"
      esmaecida={q.isPlaceholderData}
      legenda={
        <span className="flex gap-4">
          <ChaveLinha cor={DESTAQUE}>depois do método</ChaveLinha>
          <ChaveLinha cor={CONTEXTO}>comparação</ChaveLinha>
        </span>
      }
      tabela={
        <Tabela
          cabecalho={["Grupo", "Respostas", "Acertos", "Taxa"]}
          linhas={q.data.dados.map((d) => [NOME[d.grupo], d.respostas, d.acertos, porcento(d.taxa == null ? null : Number(d.taxa))])}
        />
      }
    >
      <ul className="space-y-3">
        {q.data.dados.map((d) => {
          const taxa = d.taxa == null ? null : Number(d.taxa);
          const poucas = d.respostas < POUCAS_RESPOSTAS;
          return (
            <li key={d.grupo} className="grid grid-cols-[7rem_1fr] items-center gap-3 text-sm sm:grid-cols-[9rem_1fr_11rem]">
              <span className={d.grupo === "sem_sessao" ? "text-apagado" : ""}>{NOME[d.grupo]}</span>
              <span className="h-3 rounded-r-sm" aria-hidden
                style={{ width: `${(taxa ?? 0) * 100}%`, background: d.grupo === "sem_sessao" ? CONTEXTO : DESTAQUE, opacity: poucas ? 0.45 : 1 }} />
              <span className="col-span-2 text-xs text-apagado tabular-nums sm:col-span-1 sm:text-right">
                {d.respostas === 0 ? "sem respostas" : `${porcento(taxa)} · ${d.respostas} respostas${poucas ? " (poucas)" : ""}`}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-apagado">
        Correlação, não causa: se você só faz um método quando está descansado, ele &ldquo;ganha&rdquo; por isso.
        Barras claras têm menos de {POUCAS_RESPOSTAS} respostas.
      </p>
    </Figura>
  );
}
