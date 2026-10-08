"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";

/*
 * Convenções dos gráficos (skill de dataviz): marcas finas (linha 2px, barra até
 * 24px com ponta arredondada de 4px e base reta), grade em fio sólido recessivo, um
 * eixo só, legenda para 2+ séries, texto nunca na cor da série, tooltip que
 * acrescenta (nunca é o único jeito de ler o valor: toda figura tem a tabela).
 */

const EIXO = { stroke: "var(--fio)", tick: { fill: "var(--apagado)", fontSize: 11 }, tickLine: false };

/** Moldura de figura: título, legenda opcional, o gráfico e a tabela equivalente. */
export function Figura({
  titulo,
  sobre,
  legenda,
  tabela,
  esmaecida,
  children,
}: {
  titulo: string;
  sobre?: ReactNode;
  legenda?: ReactNode;
  tabela: ReactNode;
  esmaecida?: boolean;
  children: ReactNode;
}) {
  return (
    <figure className={`mb-12 transition-opacity ${esmaecida ? "opacity-50" : ""}`}>
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-fio pb-1">
        <span>
          <span className="rotulo">{titulo}</span>
          {sobre && <span className="ml-2 text-xs text-apagado">{sobre}</span>}
        </span>
        {legenda}
      </figcaption>
      {children}
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-xs text-apagado hover:text-acento">ver como tabela</summary>
        <div className="mt-2 max-h-72 overflow-auto">{tabela}</div>
      </details>
    </figure>
  );
}

/** Chave de legenda: um traço da cor da série ao lado do texto (texto em tinta). */
export function ChaveLinha({ cor, fina, children }: { cor: string; fina?: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-apagado">
      <span aria-hidden className="inline-block w-4" style={{ height: fina ? 1 : 2, background: cor }} />
      {children}
    </span>
  );
}

export function Tabela({ cabecalho, linhas }: { cabecalho: string[]; linhas: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto">
    <table className="w-full text-left text-sm tabular-nums">
      <thead>
        <tr className="border-b border-fio text-xs text-apagado">
          {cabecalho.map((c) => (
            <th key={c} className="py-1 pr-4 font-normal">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {linhas.map((l, i) => (
          <tr key={i} className="border-b border-fio/60">
            {l.map((v, j) => (
              // 1a coluna (data), números e a última (valor) não quebram; nomes quebram
              <td key={j} className={`py-1 pr-4 ${typeof v === "number" || j === 0 || j === l.length - 1 ? "whitespace-nowrap" : ""}`}>
                {v}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

type Ponto = Record<string, string | number | null>;

function CaixaTooltip({ titulo, linhas }: { titulo: string; linhas: { cor: string; nome: string; valor: string }[] }) {
  return (
    <div className="border border-fio bg-papel px-3 py-2 text-xs">
      <p className="mb-1 text-apagado">{titulo}</p>
      {linhas.map((l) => (
        <p key={l.nome} className="flex items-center gap-2">
          <span aria-hidden className="inline-block h-0.5 w-3" style={{ background: l.cor }} />
          {/* o valor é o elemento forte; o nome da série vem depois */}
          <strong className="font-semibold text-tinta">{l.valor}</strong>
          <span className="text-apagado">{l.nome}</span>
        </p>
      ))}
    </div>
  );
}

/**
 * Linha de ÊNFASE: uma série em destaque (a média) e outra de contexto (o dia a dia,
 * ruidoso). Crosshair que segue o X e um tooltip com as duas séries.
 */
export function LinhaEnfase({
  dados,
  x,
  destaque,
  contexto,
  formatoX,
  formatoY,
}: {
  dados: Ponto[];
  x: string;
  destaque: { chave: string; nome: string };
  contexto: { chave: string; nome: string };
  formatoX: (v: string) => string;
  formatoY: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={dados} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
        <CartesianGrid vertical={false} stroke="var(--fio)" />
        <XAxis dataKey={x} {...EIXO} tickFormatter={formatoX} minTickGap={32} />
        <YAxis {...EIXO} axisLine={false} domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickFormatter={formatoY} />
        <Tooltip
          cursor={{ stroke: "var(--apagado)", strokeWidth: 1 }}
          content={({ active, payload, label }: TooltipContentProps<ValueType, NameType>) =>
            active && payload?.length ? (
              <CaixaTooltip
                titulo={formatoX(String(label))}
                linhas={[destaque, contexto].map((s) => {
                  const v = payload[0].payload[s.chave];
                  return {
                    cor: s === destaque ? "var(--grafico-destaque)" : "var(--grafico-contexto)",
                    nome: s.nome,
                    valor: v == null ? "—" : formatoY(Number(v)),
                  };
                })}
              />
            ) : null
          }
        />
        <Line
          dataKey={contexto.chave}
          stroke="var(--grafico-contexto)"
          strokeWidth={1}
          dot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Line
          dataKey={destaque.chave}
          stroke="var(--grafico-destaque)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          dot={false}
          activeDot={{ r: 4, stroke: "var(--papel)", strokeWidth: 2, fill: "var(--grafico-destaque)" }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Marcas "redondas" no eixo (0, 50, 100...): passo de 1, 2 ou 5 vezes uma potência de 10. */
export function marcasRedondas(maximo: number, quantas = 4): number[] {
  if (maximo <= 0) return [0, 1];
  const bruto = maximo / quantas;
  const potencia = 10 ** Math.floor(Math.log10(bruto));
  const passo = [1, 2, 5, 10].map((m) => m * potencia).find((p) => p >= bruto) ?? bruto;
  const marcas = [];
  for (let v = 0; v < maximo + passo; v += passo) marcas.push(v);
  return marcas;
}

/** Colunas de uma série só (uma cor; sem legenda: o título diz o que é). */
export function Colunas({
  dados,
  x,
  y,
  nome,
  formatoX,
}: {
  dados: Ponto[];
  x: string;
  y: string;
  nome: string;
  formatoX: (v: string) => string;
}) {
  const marcas = marcasRedondas(Math.max(0, ...dados.map((d) => Number(d[y]) || 0)));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={dados} margin={{ top: 8, right: 8, bottom: 0, left: -20 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--fio)" />
        <XAxis dataKey={x} {...EIXO} tickFormatter={formatoX} minTickGap={24} />
        <YAxis {...EIXO} axisLine={false} ticks={marcas} domain={[0, marcas.at(-1) ?? 1]} />
        <Tooltip
          cursor={{ fill: "var(--tarja)" }}
          content={({ active, payload, label }: TooltipContentProps<ValueType, NameType>) =>
            active && payload?.length ? (
              <CaixaTooltip
                titulo={formatoX(String(label))}
                linhas={[{ cor: "var(--grafico-destaque)", nome, valor: String(payload[0].value) }]}
              />
            ) : null
          }
        />
        <Bar dataKey={y} fill="var(--grafico-destaque)" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
