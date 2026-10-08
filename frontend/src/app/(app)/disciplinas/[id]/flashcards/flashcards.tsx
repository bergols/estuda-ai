"use client";

import { useState } from "react";

import { Custo, FormularioGerar } from "@/components/geracao";
import { Botao, Carregando, Erro, Secao, Vazio } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import { useFlashcards, useGerarFlashcards } from "@/lib/consultas";

import { useDisciplinaId } from "../topo";

export function Flashcards() {
  const id = useDisciplinaId();
  const cards = useFlashcards(id);
  const gerar = useGerarFlashcards(id);
  const [gerando, setGerando] = useState(false);

  return (
    <>
      <Secao
        titulo={cards.data ? `${cards.data.length} flashcards` : "Flashcards"}
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
              rotuloBotao="Gerar flashcards"
              quantidadePadrao={8}
              maximo={20}
              gerando={gerar.isPending}
              erro={gerar.error}
              gerar={(corpo) => gerar.mutate(corpo)}
            />
          </div>
        )}
        {gerar.data && <ResultadoGeracao resultado={gerar.data} />}

        {cards.isPending && <Carregando />}
        {cards.isError && <Erro erro={cards.error} tentarDeNovo={() => cards.refetch()} />}
        {cards.data?.length === 0 && <Vazio>Nenhum flashcard. Gere a partir de um tema ou de um material.</Vazio>}
        <ul className="divide-y divide-fio">
          {cards.data?.map((c) => <Ficha key={c.id} card={c} />)}
        </ul>
      </Secao>
    </>
  );
}

/** Frente sempre visível; o verso abre ao clicar (como virar a ficha). */
function Ficha({ card }: { card: Esquemas["FlashcardLer"] }) {
  return (
    <li>
      <details className="group py-4">
        <summary className="flex cursor-pointer list-none items-baseline justify-between gap-4">
          <span className="font-serif text-lg">{card.frente}</span>
          <span className="shrink-0 text-xs text-apagado">{card.topico}</span>
        </summary>
        <p className="mt-3 border-l-2 border-acento pl-3 leading-relaxed">{card.verso}</p>
      </details>
    </li>
  );
}

function ResultadoGeracao({ resultado }: { resultado: Esquemas["FlashcardsGeradosSaida"] }) {
  return (
    <div className="mb-8 border-l-2 border-certo pl-4 text-sm">
      <p>
        {resultado.criados.length} criado(s)
        {resultado.descartados.length > 0 &&
          `, ${resultado.descartados.length} descartado(s) por serem quase iguais a cards que você já tem`}
        .
      </p>
      {resultado.descartados.length > 0 && (
        <ul className="mt-2 space-y-1 text-apagado">
          {resultado.descartados.map((d, i) => (
            <li key={i}>
              “{d.frente}” ≈ “{d.parecido_com_frente}”{" "}
              <span className="font-mono">({(d.similaridade * 100).toFixed(0)}% parecido)</span>
            </li>
          ))}
        </ul>
      )}
      <Custo geracao={resultado.geracao} />
    </div>
  );
}
