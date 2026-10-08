"use client";

import { useState } from "react";

import { Custo, FormularioGerar } from "@/components/geracao";
import { Botao, Carregando, Erro, Secao, Vazio } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import { useGerarQuestoes, useQuestoes, useResponderQuestao } from "@/lib/consultas";

import { useDisciplinaId } from "../topo";

export function Questoes() {
  const id = useDisciplinaId();
  const questoes = useQuestoes(id);
  const gerar = useGerarQuestoes(id);
  const [gerando, setGerando] = useState(false);

  return (
    <Secao
      titulo={questoes.data ? `${questoes.data.length} questões` : "Questões"}
      acoes={
        !gerando && (
          <Botao variante="discreto" onClick={() => setGerando(true)}>
            gerar com IA
          </Botao>
        )
      }
    >
      {gerando && (
        <div className="mb-6">
          <FormularioGerar
            disciplinaId={id}
            rotuloBotao="Gerar questões"
            quantidadePadrao={3}
            maximo={10}
            gerando={gerar.isPending}
            erro={gerar.error}
            gerar={(corpo) => gerar.mutate(corpo)}
          />
        </div>
      )}
      {gerar.data && (
        <div className="mb-8 border-l-2 border-certo pl-4 text-sm">
          {gerar.data.questoes.length} questão(ões) nova(s) no topo da lista.
          <Custo geracao={gerar.data.geracao} />
        </div>
      )}

      {questoes.isPending && <Carregando />}
      {questoes.isError && <Erro erro={questoes.error} tentarDeNovo={() => questoes.refetch()} />}
      {questoes.data?.length === 0 && <Vazio>Nenhuma questão. Gere algumas para treinar.</Vazio>}
      <ol className="divide-y divide-fio">
        {questoes.data?.map((q, i) => <Questao key={q.id} numero={i + 1} questao={q} />)}
      </ol>
    </Secao>
  );
}

function Questao({ numero, questao }: { numero: number; questao: Esquemas["QuestaoLer"] }) {
  const id = useDisciplinaId();
  const responder = useResponderQuestao(id);
  // Tempo de resposta: do momento em que a questão apareceu até o clique. O
  // inicializador preguiçoso roda uma vez só; o fim vem do timeStamp do próprio evento,
  // que está na mesma escala de performance.now().
  const [inicio] = useState(() => performance.now());
  const resultado = responder.data;

  function escolher(letra: Esquemas["TentativaEntrada"]["alternativa"], quando: number) {
    if (resultado || responder.isPending) return;
    responder.mutate({ questaoId: questao.id, alternativa: letra, tempo_ms: Math.round(quando - inicio) });
  }

  return (
    <li className="py-6">
      <p className="mb-4 font-serif text-lg leading-relaxed">
        <span className="mr-2 font-mono text-sm text-apagado">{numero}.</span>
        {questao.enunciado}
      </p>
      <ul className="space-y-1">
        {questao.alternativas.map((a) => {
          const letra = a.letra as Esquemas["TentativaEntrada"]["alternativa"];
          const correta = resultado?.alternativa_correta === letra;
          const escolhidaErrada = resultado && !resultado.correta && resultado.alternativa_escolhida === letra;
          return (
            <li key={letra}>
              <button
                type="button"
                onClick={(e) => escolher(letra, e.timeStamp)}
                disabled={!!resultado || responder.isPending}
                className={`grid w-full grid-cols-[1.75rem_1fr] gap-2 px-2 py-2 text-left transition-colors ${
                  correta
                    ? "bg-tarja text-certo"
                    : escolhidaErrada
                      ? "bg-alerta text-errado line-through"
                      : resultado
                        ? "text-apagado"
                        : "hover:bg-tarja"
                }`}
              >
                <span className="font-mono">{letra})</span>
                <span>{a.texto}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {responder.isError && <Erro erro={responder.error} />}
      {resultado && (
        <div className={`mt-4 border-l-2 pl-3 text-sm ${resultado.correta ? "border-certo" : "border-errado"}`}>
          <p className="font-medium">{resultado.correta ? "Certa." : `Errada: a resposta é ${resultado.alternativa_correta}.`}</p>
          {resultado.explicacao && <p className="mt-1 leading-relaxed text-apagado">{resultado.explicacao}</p>}
        </div>
      )}
    </li>
  );
}
