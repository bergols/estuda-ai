"use client";

import { useState, type FormEvent } from "react";

import { Botao, Carregando, Erro, Vazio } from "@/components/ui";
import { useBusca, type Modo } from "@/lib/consultas";
import { paginas } from "@/lib/formato";

import { useDisciplinaId } from "../topo";

const MODOS: { valor: Modo; rotulo: string; explica: string }[] = [
  { valor: "hibrida", rotulo: "Híbrida", explica: "combina as duas (RRF)" },
  { valor: "semantica", rotulo: "Semântica", explica: "pelo sentido (embeddings + HNSW)" },
  { valor: "textual", rotulo: "Textual", explica: "pelas palavras (full-text + GIN)" },
];

export function Busca() {
  const id = useDisciplinaId();
  const [modo, setModo] = useState<Modo>("hibrida");
  const [termo, setTermo] = useState("");
  const busca = useBusca(id, termo, modo);

  function buscar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setTermo(String(new FormData(evento.currentTarget).get("q") ?? "").trim());
  }

  return (
    <>
      <form onSubmit={buscar} className="mb-4 flex gap-3">
        <label className="min-w-0 flex-1">
          <span className="sr-only">Buscar nos materiais</span>
          <input
            name="q"
            type="search"
            required
            maxLength={500}
            placeholder="como desfazer uma transação?"
            className="w-full border-0 border-b border-tinta bg-transparent px-0 py-2 font-serif text-xl placeholder:text-apagado focus:border-acento focus:ring-0 focus:outline-none"
          />
        </label>
        <Botao type="submit">Buscar</Botao>
      </form>

      <fieldset className="mb-8 flex flex-wrap gap-x-6 gap-y-2 text-sm">
        <legend className="sr-only">Modo de busca</legend>
        {MODOS.map((m) => (
          <label key={m.valor} className="flex cursor-pointer items-baseline gap-2">
            <input
              type="radio"
              name="modo"
              value={m.valor}
              checked={modo === m.valor}
              onChange={() => setModo(m.valor)}
              className="accent-[var(--acento)]"
            />
            <span className={modo === m.valor ? "text-tinta" : "text-apagado"}>
              {m.rotulo} <span className="text-xs text-apagado">— {m.explica}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {busca.isFetching && <Carregando texto="Buscando" />}
      {busca.isError && <Erro erro={busca.error} tentarDeNovo={() => busca.refetch()} />}
      {busca.data?.length === 0 && <Vazio>Nada encontrado para “{termo}”.</Vazio>}

      <ol className="space-y-6">
        {busca.data?.map((r, i) => (
          <li key={r.trecho_id} className="grid grid-cols-[2rem_1fr] gap-x-3">
            <span className="pt-0.5 font-mono text-sm text-apagado">{i + 1}.</span>
            <div className="min-w-0">
              <p className="mb-1 text-sm text-apagado">
                {r.material_titulo}
                {paginas(r.pagina, r.pagina_fim) && ` · ${paginas(r.pagina, r.pagina_fim)}`}
                <span className="font-mono"> · score {r.score.toFixed(3)}</span>
              </p>
              <p className="border-l border-fio pl-3 leading-relaxed">{r.conteudo}</p>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}
