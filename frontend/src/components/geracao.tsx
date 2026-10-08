"use client";

import { useState, type FormEvent } from "react";

import type { Esquemas } from "@/lib/api";
import { useMateriais } from "@/lib/consultas";

import { Botao, Campo, Erro } from "./ui";

/** Quanto custou a chamada ao LLM (linha da tabela geracoes). */
export function Custo({ geracao }: { geracao: Esquemas["GeracaoResumo"] | null | undefined }) {
  if (!geracao) return null;
  const custo = geracao.custo_usd == null ? "custo desconhecido" : `US$ ${Number(geracao.custo_usd).toFixed(4)}`;
  return (
    <p className="mt-2 font-mono text-xs text-apagado">
      {geracao.modelo} · {geracao.tokens_entrada + geracao.tokens_saida} tokens · {custo} ·{" "}
      {(geracao.duracao_ms / 1000).toFixed(1)} s{geracao.chamadas > 1 && " · 2 tentativas"}
    </p>
  );
}

/**
 * Formulário comum de "gerar com IA": a partir de UM material inteiro ou de um tema
 * (que vira uma busca híbrida). A API exige exatamente um dos dois.
 */
export function FormularioGerar({
  disciplinaId,
  rotuloBotao,
  quantidadePadrao,
  maximo,
  gerando,
  erro,
  gerar,
}: {
  disciplinaId: number;
  rotuloBotao: string;
  quantidadePadrao: number;
  maximo: number;
  gerando: boolean;
  erro: unknown;
  gerar: (corpo: Esquemas["GerarEntrada"]) => void;
}) {
  const materiais = useMateriais(disciplinaId);
  const prontos = materiais.data?.filter((m) => m.status === "concluido") ?? [];
  const [fonte, setFonte] = useState<"tema" | "material">("tema");

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const form = new FormData(evento.currentTarget);
    const quantidade = Number(form.get("quantidade"));
    gerar(
      fonte === "tema"
        ? { tema: String(form.get("tema")).trim(), quantidade }
        : { material_id: Number(form.get("material_id")), quantidade },
    );
  }

  return (
    <form onSubmit={enviar} className="space-y-5 bg-tarja p-5">
      <fieldset className="flex gap-6 text-sm">
        <legend className="rotulo mb-2">A partir de</legend>
        {(["tema", "material"] as const).map((valor) => (
          <label key={valor} className="flex cursor-pointer items-center gap-2">
            <input
              type="radio"
              name="fonte"
              checked={fonte === valor}
              onChange={() => setFonte(valor)}
              disabled={valor === "material" && prontos.length === 0}
              className="accent-[var(--acento)]"
            />
            {valor === "tema" ? "um tema" : "um material inteiro"}
          </label>
        ))}
      </fieldset>
      <div className="grid gap-5 sm:grid-cols-[1fr_8rem]">
        {fonte === "tema" ? (
          <Campo rotulo="Tema" name="tema" required maxLength={300} placeholder="índices B-tree e quando usá-los" />
        ) : (
          <label className="block">
            <span className="rotulo">Material</span>
            <select
              name="material_id"
              required
              className="w-full border-0 border-b border-fio bg-transparent px-0 py-2 focus:border-acento focus:ring-0"
            >
              {prontos.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.titulo}
                </option>
              ))}
            </select>
          </label>
        )}
        <Campo rotulo="Quantos" name="quantidade" type="number" min={1} max={maximo} defaultValue={quantidadePadrao} required />
      </div>
      {erro != null && <Erro erro={erro} />}
      <Botao type="submit" carregando={gerando}>
        {gerando ? "Gerando… (pode levar uns segundos)" : rotuloBotao}
      </Botao>
    </form>
  );
}
