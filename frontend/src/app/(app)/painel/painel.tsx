"use client";

import { useState } from "react";

import { ChaveLinha, Colunas, Figura, LinhaEnfase, Tabela } from "@/components/graficos";
import { TextoRico } from "@/components/texto-rico";
import { Botao, Cabecalho, Carregando, Erro, Vazio } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import {
  useAtualizarAnalytics,
  useCalendario,
  useCardsDificeis,
  useCustos,
  useDisciplinas,
  useEvolucaoDiaria,
  useEvolucaoSemanal,
  usePrevisao,
  useSequencia,
} from "@/lib/consultas";
import { dia, mes, porcento, usd } from "@/lib/formato";

import { SecaoFoco } from "./foco";

const pct = (v: number) => `${Math.round(v * 100)}%`;
const hora = (iso: string) => new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

export function Painel() {
  const [disciplinaId, setDisciplinaId] = useState<number | undefined>();
  const disciplinas = useDisciplinas();
  const evolucao = useEvolucaoDiaria(disciplinaId);
  const atualizar = useAtualizarAnalytics();

  return (
    <>
      <Cabecalho sobre="Desempenho" titulo="Painel" />
      {/* Um filtro, numa linha, acima de tudo o que ele afeta */}
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4 text-sm">
        <select
          aria-label="Disciplina"
          value={disciplinaId ?? ""}
          onChange={(e) => setDisciplinaId(e.target.value ? Number(e.target.value) : undefined)}
          className="border-0 border-b border-tinta bg-transparent py-1 focus:border-acento focus:ring-0"
        >
          <option value="">Todas as disciplinas</option>
          {disciplinas.data?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.nome}
            </option>
          ))}
        </select>
        <span className="flex items-center gap-3 text-xs text-apagado">
          {/* O histórico vem de uma materialized view: mostra de quando é a foto */}
          {evolucao.data?.atualizado_em && <>histórico de {hora(evolucao.data.atualizado_em)}</>}
          <Botao variante="discreto" carregando={atualizar.isPending} onClick={() => atualizar.mutate()}>
            atualizar
          </Botao>
        </span>
      </div>
      {atualizar.isError && <Erro erro={atualizar.error} />}

      <Numeros disciplinaId={disciplinaId} />
      <AcertoDiario disciplinaId={disciplinaId} />
      <Calendario disciplinaId={disciplinaId} />
      <Previsao disciplinaId={disciplinaId} />
      <CardsDificeis disciplinaId={disciplinaId} />
      <SecaoFoco disciplinaId={disciplinaId} />
      <Custos disciplinaId={disciplinaId} />
    </>
  );
}

// ------------------------------------------------------------------ números

function Numero({ rotulo, valor, nota }: { rotulo: string; valor: string; nota?: React.ReactNode }) {
  // Figura em SEM serifa e com algarismos proporcionais (a skill de dataviz: serifa no
  // número parece enfeite; tabular-nums só onde números se alinham em coluna).
  return (
    <div className="py-3 pr-4 sm:pl-4 sm:first:pl-0">
      <p className="rotulo">{rotulo}</p>
      <p className="mt-1 font-sans text-3xl font-semibold">{valor}</p>
      {nota && <p className="mt-1 text-xs text-apagado">{nota}</p>}
    </div>
  );
}

function Numeros({ disciplinaId }: { disciplinaId?: number }) {
  const sequencia = useSequencia(disciplinaId);
  const semanal = useEvolucaoSemanal(disciplinaId);
  const previsao = usePrevisao(disciplinaId);

  if (sequencia.isError) return <Erro erro={sequencia.error} tentarDeNovo={() => sequencia.refetch()} />;
  const s = sequencia.data;
  const semanas = semanal.data?.dados ?? [];
  const atual = semanas.at(-1);
  const variacao = atual?.variacao_pp == null ? null : Number(atual.variacao_pp);
  const hoje = previsao.data?.dados[0];

  return (
    <div className="mb-12 grid grid-cols-2 divide-fio border-y border-fio sm:grid-cols-4 sm:divide-x">
      <Numero
        rotulo="Sequência"
        valor={s ? `${s.atual_dias} d` : "…"}
        nota={s && (s.estudou_hoje ? "estudou hoje" : s.atual_dias > 0 ? "estude hoje para não perder" : "comece hoje")}
      />
      <Numero rotulo="Maior sequência" valor={s ? `${s.maior_dias} d` : "…"} nota={s && `${s.dias_estudados} dias estudados`} />
      <Numero
        rotulo="Acerto na semana"
        valor={atual ? porcento(atual.taxa == null ? null : Number(atual.taxa)) : "…"}
        nota={
          variacao != null && (
            // Cor + sinal + texto: a direção nunca depende só da cor
            <span className={variacao >= 0 ? "text-certo" : "text-errado"}>
              {variacao >= 0 ? "▲ +" : "▼ "}
              {variacao.toFixed(1)} p.p. sobre a anterior
            </span>
          )
        }
      />
      <Numero
        rotulo="Para hoje"
        valor={hoje ? String(hoje.cards) : "…"}
        nota={hoje && hoje.atrasados > 0 ? `${hoje.atrasados} atrasados` : "cards na fila"}
      />
    </div>
  );
}

// ------------------------------------------------------------- acerto diário

function AcertoDiario({ disciplinaId }: { disciplinaId?: number }) {
  const q = useEvolucaoDiaria(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const pontos = q.data.dados.map((d) => ({
    dia: d.dia,
    taxa: d.taxa == null ? null : Number(d.taxa),
    media: d.taxa_media_7d == null ? null : Number(d.taxa_media_7d),
  }));
  if (!q.data.dados.some((d) => d.respostas > 0)) return <Vazio>Sem respostas no período.</Vazio>;
  return (
    <Figura
      titulo="Acerto"
      sobre="cards e questões, por dia"
      esmaecida={q.isPlaceholderData}
      legenda={
        <span className="flex gap-4">
          <ChaveLinha cor="var(--grafico-destaque)">média de 7 dias</ChaveLinha>
          <ChaveLinha cor="var(--grafico-contexto)" fina>
            no dia
          </ChaveLinha>
        </span>
      }
      tabela={
        <Tabela
          cabecalho={["Dia", "Respostas", "Acerto", "Média 7 d"]}
          linhas={q.data.dados.map((d) => [dia(d.dia), d.respostas, porcento(d.taxa == null ? null : Number(d.taxa)), porcento(d.taxa_media_7d == null ? null : Number(d.taxa_media_7d))])}
        />
      }
    >
      <LinhaEnfase
        dados={pontos}
        x="dia"
        destaque={{ chave: "media", nome: "média de 7 dias" }}
        contexto={{ chave: "taxa", nome: "no dia" }}
        formatoX={dia}
        formatoY={pct}
      />
    </Figura>
  );
}

// --------------------------------------------------------------- calendário

/** Uma cor só, do mais claro (pouco) ao mais escuro (muito): nível 0 a 4 da API. */
const NIVEL = ["var(--tarja)", ...[30, 55, 78, 100].map((p) => `color-mix(in oklab, var(--grafico-destaque) ${p}%, var(--papel))`)];

function Calendario({ disciplinaId }: { disciplinaId?: number }) {
  const q = useCalendario(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const dias = q.data.dados;
  if (dias.length === 0) return null;

  // Colunas = semanas; linhas = dia da semana (0 = domingo, como o dia_semana da API)
  const lado = 11;
  const passo = lado + 2; // 2px de papel entre as células (o "respiro", sem borda)
  const deslocamento = dias[0].dia_semana;
  const colunas = Math.ceil((dias.length + deslocamento) / 7);
  const total = dias.reduce((s, d) => s + d.revisoes, 0);

  return (
    <Figura
      titulo="Calendário"
      sobre={`${total} revisões no último ano`}
      esmaecida={q.isPlaceholderData}
      legenda={
        <span className="flex items-center gap-1 text-xs text-apagado">
          menos
          {NIVEL.map((cor, i) => (
            <span key={i} aria-hidden className="inline-block size-2.5" style={{ background: cor }} />
          ))}
          mais
        </span>
      }
      tabela={
        <Tabela
          cabecalho={["Dia", "Revisões"]}
          linhas={dias.filter((d) => d.revisoes > 0).map((d) => [dia(d.dia), d.revisoes])}
        />
      }
    >
      {/* dir="rtl" no contêiner: a rolagem começa na DIREITA (os dias recentes) no
          celular. As coordenadas do SVG não espelham, o desenho fica igual. */}
      <div dir="rtl" className="overflow-x-auto [scrollbar-width:thin]">
        <svg
          width={colunas * passo}
          height={7 * passo}
          role="img"
          aria-label={`Revisões por dia no último ano: ${total} no total`}
        >
          {dias.map((d, i) => {
            const pos = i + deslocamento;
            return (
              <rect
                key={d.dia}
                x={Math.floor(pos / 7) * passo}
                y={(pos % 7) * passo}
                width={lado}
                height={lado}
                rx={2}
                fill={NIVEL[d.nivel]}
              >
                <title>{`${dia(d.dia)}: ${d.revisoes} revisões`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
    </Figura>
  );
}

// ------------------------------------------------------------------ previsão

function Previsao({ disciplinaId }: { disciplinaId?: number }) {
  const q = usePrevisao(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  return (
    <Figura
      titulo="Próximos 30 dias"
      sobre="cards que vencem por dia (hoje inclui os atrasados)"
      esmaecida={q.isPlaceholderData}
      tabela={<Tabela cabecalho={["Dia", "Cards", "Atrasados"]} linhas={q.data.dados.map((d) => [dia(d.dia), d.cards, d.atrasados])} />}
    >
      <Colunas dados={q.data.dados} x="dia" y="cards" nome="cards" formatoX={dia} />
    </Figura>
  );
}

// ---------------------------------------------------------- cards difíceis

function CardsDificeis({ disciplinaId }: { disciplinaId?: number }) {
  const q = useCardsDificeis(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const cards: Esquemas["CardDificil"][] = q.data.dados;
  return (
    <section className={`mb-12 transition-opacity ${q.isPlaceholderData ? "opacity-50" : ""}`}>
      <h2 className="rotulo mb-3 border-b border-fio pb-1">Cards mais difíceis</h2>
      {cards.length === 0 ? (
        <Vazio>Ainda sem revisões suficientes.</Vazio>
      ) : (
        // Uma lista ranqueada é tabela, não gráfico
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-fio text-xs text-apagado">
              <th className="w-8 py-1 font-normal">#</th>
              <th className="py-1 font-normal">Card</th>
              <th className="py-1 text-right font-normal">Erro</th>
              <th className="hidden py-1 text-right font-normal sm:table-cell">Revisões</th>
            </tr>
          </thead>
          <tbody>
            {cards.map((c) => (
              <tr key={c.flashcard_id} className="border-b border-fio/60 align-baseline">
                <td className="py-2 font-mono text-apagado tabular-nums">{c.posicao_rank}</td>
                <td className="py-2">
                  <TextoRico>{c.frente}</TextoRico>
                  <span className="block text-xs text-apagado">
                    {c.disciplina}
                    {c.topico && ` · ${c.topico}`}
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums">{porcento(Number(c.taxa_erro))}</td>
                <td className="hidden py-2 text-right text-apagado tabular-nums sm:table-cell">{c.revisoes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// -------------------------------------------------------------------- custos

const TIPO: Record<string, string> = { pergunta: "perguntas", flashcards: "flashcards", questoes: "questões" };

function Custos({ disciplinaId }: { disciplinaId?: number }) {
  const q = useCustos(disciplinaId);
  if (q.isPending) return <Carregando />;
  if (q.isError) return <Erro erro={q.error} tentarDeNovo={() => q.refetch()} />;
  const linhas = q.data.dados;
  const total = linhas.at(-1)?.acumulado_total ?? 0;
  return (
    <section className={`mb-12 transition-opacity ${q.isPlaceholderData ? "opacity-50" : ""}`}>
      <h2 className="rotulo mb-3 flex justify-between border-b border-fio pb-1">
        <span>Gasto com IA</span>
        <span className="normal-case tracking-normal">acumulado {usd(total)}</span>
      </h2>
      {linhas.length === 0 ? (
        <Vazio>Nenhuma geração com IA ainda.</Vazio>
      ) : (
        <Tabela
          cabecalho={["Mês", "Tipo", "Gerações", "Falhas", "Custo"]}
          linhas={linhas.map((l) => [
            mes(l.mes),
            TIPO[l.tipo] ?? l.tipo,
            l.geracoes,
            l.falhas,
            usd(l.custo_usd),
          ])}
        />
      )}
    </section>
  );
}
