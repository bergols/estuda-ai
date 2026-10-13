"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

import { TextoRico } from "@/components/texto-rico";
import { Botao } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import { filtrar, limitar, materiaisDoBaralho } from "@/lib/baralho";
import { materialDe } from "@/lib/ordem-conteudo";

type Card = Esquemas["FlashcardLer"];

const SELECT =
  "border-0 border-b border-fio bg-transparent px-0 py-1 text-sm text-tinta focus:border-acento focus:outline-none focus:ring-0";

/** Onde parou, por disciplina e material: abrir de novo continua do mesmo card. */
const chaveDaPosicao = (disciplina: number, material: string) => `estuda-ai:baralho:${disciplina}:${material}`;

function lerPosicao(chave: string): number {
  try {
    return Number(localStorage.getItem(chave) ?? 0) || 0;
  } catch {
    return 0;
  }
}

/**
 * Um flashcard por vez, como um baralho de fichas. Virar: clique na ficha, espaço ou
 * Enter. Passar: ← →, os botões ou deslizar o dedo. A ordem é a da matéria.
 */
export function Baralho({ cards, nomes, disciplina }: { cards: Card[]; nomes: Map<number, string>; disciplina: number }) {
  const materiais = materiaisDoBaralho(cards, nomes);
  const [material, setMaterial] = useState<string>("todos");
  const selecionados = filtrar(cards, material === "todos" ? "todos" : material === "nenhum" ? null : Number(material));
  const chave = chaveDaPosicao(disciplina, material);
  const [indice, setIndice] = useState(() => lerPosicao(chave));
  const [virado, setVirado] = useState(false);
  const atual = limitar(indice, selecionados.length);
  const card = selecionados[atual];

  // Trocar de material: volta para onde parou NAQUELE material
  function escolherMaterial(valor: string) {
    setMaterial(valor);
    setIndice(lerPosicao(chaveDaPosicao(disciplina, valor)));
    setVirado(false);
  }

  const total = selecionados.length;
  // Sem useCallback: o React Compiler do projeto já memoriza
  function ir(novo: number) {
    const i = limitar(novo, total);
    setIndice(i);
    setVirado(false);
    try {
      localStorage.setItem(chave, String(i));
    } catch {
      // sem localStorage: só não lembra a posição
    }
  }

  // Teclado (fora de campos de texto/seleção). useEffectEvent: o ouvinte é registrado
  // uma vez e sempre enxerga a posição atual, sem re-registrar a cada render
  const teclar = useEffectEvent((e: KeyboardEvent) => {
    const alvo = e.target as HTMLElement;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(alvo.tagName) || alvo.isContentEditable) return;
    if (e.key === "ArrowRight") ir(atual + 1);
    else if (e.key === "ArrowLeft") ir(atual - 1);
    // Com um botão focado, espaço/Enter já "clicam" nele (inclusive na ficha): não virar duas vezes
    else if ((e.key === " " || e.key === "Enter") && alvo.tagName !== "BUTTON") {
      e.preventDefault();
      setVirado((v) => !v);
    }
  });
  useEffect(() => {
    const ouvir = (e: KeyboardEvent) => teclar(e);
    window.addEventListener("keydown", ouvir);
    return () => window.removeEventListener("keydown", ouvir);
  }, []);

  // Deslizar o dedo (celular): mais de 50 px na horizontal passa de card
  const inicioToque = useRef<number | null>(null);
  const deslizou = useRef(false); // o clique que vem depois de deslizar não vira a ficha

  if (!card) return null;
  const nomeDoMaterial = materiais.find((m) => m.id === materialDe(card))?.nome;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <label className="flex items-baseline gap-2 text-sm">
          <span className="rotulo">Material</span>
          <select className={SELECT} value={material} onChange={(e) => escolherMaterial(e.target.value)}>
            <option value="todos">Todos ({cards.length})</option>
            {materiais.map((m) => (
              <option key={m.id ?? "nenhum"} value={m.id ?? "nenhum"}>
                {m.nome} ({m.quantidade})
              </option>
            ))}
          </select>
        </label>
        <span className="font-mono text-sm tabular-nums text-apagado" aria-live="polite">
          {atual + 1} / {selecionados.length}
        </span>
      </div>

      <div className="h-0.5 w-full bg-fio" aria-hidden>
        <div className="h-full bg-acento transition-[width]" style={{ width: `${((atual + 1) / selecionados.length) * 100}%` }} />
      </div>

      <button
        type="button"
        onClick={() => {
          if (deslizou.current) deslizou.current = false;
          else setVirado((v) => !v);
        }}
        onPointerDown={(e) => (inicioToque.current = e.clientX)}
        onPointerUp={(e) => {
          const inicio = inicioToque.current;
          inicioToque.current = null;
          if (inicio === null) return;
          const dx = e.clientX - inicio;
          if (Math.abs(dx) > 50) {
            deslizou.current = true;
            ir(atual + (dx < 0 ? 1 : -1));
          }
        }}
        aria-label={virado ? "Resposta; clique para ver a pergunta" : "Pergunta; clique para ver a resposta"}
        className={`flex min-h-72 w-full touch-pan-y flex-col justify-between gap-6 border px-6 py-6 text-left transition-colors sm:px-10 ${
          virado ? "border-acento bg-tarja" : "border-tinta"
        }`}
      >
        <span className="flex items-baseline justify-between gap-4 text-xs text-apagado">
          <span>{[nomeDoMaterial, card.topico].filter(Boolean).join(" · ")}</span>
          <span className="rotulo">{virado ? "resposta" : "pergunta"}</span>
        </span>
        {virado ? (
          <span className="space-y-4">
            <TextoRico className="block text-sm text-apagado">{card.frente}</TextoRico>
            <TextoRico className="block text-lg leading-relaxed">{card.verso}</TextoRico>
          </span>
        ) : (
          <TextoRico className="block text-center font-serif text-2xl leading-snug">{card.frente}</TextoRico>
        )}
        <span className="text-center text-xs text-apagado">{virado ? "" : "clique, espaço ou Enter para virar"}</span>
      </button>

      <div className="flex items-center justify-between gap-3">
        <Botao variante="secundario" onClick={() => ir(atual - 1)} disabled={atual === 0}>
          ← anterior
        </Botao>
        <Botao variante="discreto" onClick={() => setVirado((v) => !v)}>
          {virado ? "ver pergunta" : "virar"}
        </Botao>
        <Botao onClick={() => ir(atual + 1)} disabled={atual === selecionados.length - 1}>
          próximo →
        </Botao>
      </div>
    </div>
  );
}
