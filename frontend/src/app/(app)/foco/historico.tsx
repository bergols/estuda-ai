"use client";

import { Carregando, Erro, Vazio } from "@/components/ui";
import { useSessoes } from "@/lib/consultas";
import { dia, duracao, hora } from "@/lib/formato";

import { NOMES_METODO } from "./configuracao";

const STATUS = { concluida: "Concluída", abandonada: "Abandonada", em_andamento: "Em andamento" } as const;

export function Historico() {
  const sessoes = useSessoes();
  if (sessoes.isPending) return <Carregando />;
  if (sessoes.isError) return <Erro erro={sessoes.error} tentarDeNovo={() => sessoes.refetch()} />;
  if (sessoes.data.length === 0) return <Vazio>Nenhuma sessão ainda.</Vazio>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-sm">
        <thead>
          <tr className="border-b border-tinta text-left">
            <th className="rotulo py-2 pr-4 font-normal">Quando</th>
            <th className="rotulo py-2 pr-4 font-normal">Método</th>
            <th className="rotulo py-2 pr-4 font-normal">Disciplina</th>
            <th className="rotulo py-2 pr-4 text-right font-normal">Foco efetivo</th>
            <th className="rotulo py-2 pr-4 text-right font-normal">Interrupções</th>
            <th className="rotulo py-2 font-normal">Situação</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-fio">
          {sessoes.data.map((s) => (
            <tr key={s.id}>
              <td className="py-2 pr-4 whitespace-nowrap">{dia(s.dia)}, {hora(s.iniciada_em)}</td>
              <td className="py-2 pr-4">{NOMES_METODO[s.metodo]}</td>
              <td className="py-2 pr-4">{s.disciplina ?? <span className="text-apagado">—</span>}</td>
              <td className="py-2 pr-4 text-right font-mono tabular-nums">
                {duracao(s.foco_efetivo_s)}
                <span className="text-apagado"> / {duracao(s.duracao_planejada_s)}</span>
              </td>
              <td className="py-2 pr-4 text-right font-mono tabular-nums">{s.interrupcoes}</td>
              <td className={`py-2 ${s.status === "abandonada" ? "text-apagado" : ""}`}>{STATUS[s.status]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
