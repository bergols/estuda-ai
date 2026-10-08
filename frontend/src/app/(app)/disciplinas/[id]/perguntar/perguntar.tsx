"use client";

import { type FormEvent } from "react";

import { Custo } from "@/components/geracao";
import { AreaTexto, Botao, Erro, Secao } from "@/components/ui";
import { usePerguntar } from "@/lib/consultas";
import { paginas } from "@/lib/formato";

import { useDisciplinaId } from "../topo";

export function Perguntar() {
  const id = useDisciplinaId();
  const perguntar = usePerguntar(id);

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const pergunta = String(new FormData(evento.currentTarget).get("pergunta")).trim();
    if (pergunta) perguntar.mutate(pergunta);
  }

  const r = perguntar.data;
  return (
    <>
      <form onSubmit={enviar} className="mb-8 space-y-4">
        <AreaTexto
          rotulo="Pergunte aos seus materiais"
          name="pergunta"
          required
          rows={3}
          maxLength={2000}
          placeholder="O que acontece com as alterações de uma transação depois de um ROLLBACK?"
          className="font-serif text-lg"
        />
        <div className="flex items-center justify-between gap-4">
          <p className="text-xs text-apagado">A resposta usa só os trechos encontrados nos seus PDFs, com as fontes.</p>
          <Botao type="submit" carregando={perguntar.isPending}>
            {perguntar.isPending ? "Pensando…" : "Perguntar"}
          </Botao>
        </div>
      </form>

      {perguntar.isError && <Erro erro={perguntar.error} />}

      {r && (
        <article>
          <Secao titulo={r.encontrado ? "Resposta" : "Não encontrado nos materiais"}>
            <p className="font-serif text-lg leading-relaxed whitespace-pre-line">{r.resposta}</p>
            <Custo geracao={r.geracao} />
          </Secao>
          {r.citacoes.length > 0 && (
            <Secao titulo="Fontes">
              {/* Cada fonte abre o trecho citado: dá para conferir se a resposta é fiel */}
              <ol className="divide-y divide-fio">
                {r.citacoes.map((c, i) => (
                  <li key={c.trecho_id}>
                    <details className="group py-3">
                      <summary className="flex cursor-pointer list-none items-baseline gap-3">
                        <span className="font-mono text-sm text-acento">[{i + 1}]</span>
                        <span className="min-w-0 flex-1">
                          {c.material_titulo}
                          {paginas(c.pagina, c.pagina_fim) && (
                            <span className="text-apagado"> · {paginas(c.pagina, c.pagina_fim)}</span>
                          )}
                        </span>
                        <span className="text-xs text-apagado group-open:hidden">ver trecho</span>
                        <span className="hidden text-xs text-apagado group-open:inline">fechar</span>
                      </summary>
                      <blockquote className="mt-2 ml-9 border-l border-fio pl-3 text-sm leading-relaxed text-apagado">
                        {c.trecho}…
                      </blockquote>
                    </details>
                  </li>
                ))}
              </ol>
            </Secao>
          )}
        </article>
      )}
    </>
  );
}
