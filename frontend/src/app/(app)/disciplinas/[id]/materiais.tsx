"use client";

import { useRef, useState, type FormEvent } from "react";

import { Botao, Campo, Carregando, Erro, Secao, Vazio } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import { useApagarMaterial, useEnviarMaterial, useMateriais, useReprocessar } from "@/lib/consultas";
import { bytes, data } from "@/lib/formato";

import { useDisciplinaId } from "./topo";

const STATUS: Record<Esquemas["MaterialLer"]["status"], { texto: string; classe: string }> = {
  pendente: { texto: "na fila", classe: "text-apagado animate-pulse" },
  processando: { texto: "processando…", classe: "text-apagado animate-pulse" },
  concluido: { texto: "pronto", classe: "text-certo" },
  erro: { texto: "erro", classe: "text-errado" },
};

export function Materiais() {
  const id = useDisciplinaId();
  const materiais = useMateriais(id);
  const reprocessar = useReprocessar(id);
  const apagar = useApagarMaterial(id);

  return (
    <>
      <Envio disciplinaId={id} />
      <Secao titulo="Materiais">
        {materiais.isPending && <Carregando />}
        {materiais.isError && <Erro erro={materiais.error} tentarDeNovo={() => materiais.refetch()} />}
        {materiais.data?.length === 0 && <Vazio>Nenhum material. Envie um PDF da matéria acima.</Vazio>}
        {(reprocessar.isError || apagar.isError) && <Erro erro={reprocessar.error ?? apagar.error} />}
        <ul className="divide-y divide-fio">
          {materiais.data?.map((m) => (
            <li key={m.id} className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 py-3">
              <span className="min-w-0 font-serif text-lg break-words">{m.titulo}</span>
              <span className={`text-right text-sm ${STATUS[m.status].classe}`}>{STATUS[m.status].texto}</span>
              <span className="text-sm text-apagado">
                {[m.num_paginas && `${m.num_paginas} p.`, bytes(m.tamanho_bytes), data(m.criado_em)]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              <span className="flex justify-end gap-4 text-sm">
                {m.status === "erro" && (
                  <Botao variante="discreto" onClick={() => reprocessar.mutate(m.id)}>
                    reprocessar
                  </Botao>
                )}
                <Botao
                  variante="discreto"
                  onClick={() => {
                    if (confirm(`Apagar "${m.titulo}"? Trechos, e as ligações dos cards com eles, vão junto.`)) {
                      apagar.mutate(m.id);
                    }
                  }}
                >
                  apagar
                </Botao>
              </span>
              {m.status === "erro" && m.erro_mensagem && (
                <span className="col-span-2 border-l-2 border-errado bg-alerta px-3 py-1 text-sm">{m.erro_mensagem}</span>
              )}
            </li>
          ))}
        </ul>
      </Secao>
    </>
  );
}

/**
 * Na Vercel, o corpo de uma requisição a uma função (o BFF) tem no máximo 4,5 MB: um PDF
 * maior nem chega à API (erro 413). O limite vem de NEXT_PUBLIC_LIMITE_UPLOAD_MB (padrão
 * 4); no modo "tudo na VM", suba para 20 (o MAX_UPLOAD_MB da API).
 */
const LIMITE_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_LIMITE_UPLOAD_MB ?? 4);

function Envio({ disciplinaId }: { disciplinaId: number }) {
  const enviar = useEnviarMaterial(disciplinaId);
  const [grande, setGrande] = useState<string | null>(null);
  const formulario = useRef<HTMLFormElement>(null);
  const [nomeArquivo, setNomeArquivo] = useState<string | null>(null);

  function submeter(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const form = new FormData(evento.currentTarget);
    const arquivo = form.get("arquivo");
    if (!(arquivo instanceof File) || arquivo.size === 0) return;
    if (arquivo.size > LIMITE_UPLOAD_MB * 1024 * 1024) {
      setGrande(
        `O PDF tem ${(arquivo.size / 1024 ** 2).toFixed(1)} MB; o limite aqui é ${LIMITE_UPLOAD_MB} MB. ` +
          "Comprima o PDF (ex.: “Reduzir tamanho” no Preview do Mac) ou divida em partes.",
      );
      return;
    }
    setGrande(null);
    const titulo = String(form.get("titulo") ?? "").trim() || undefined;
    enviar.mutate(
      { arquivo, titulo },
      {
        onSuccess: () => {
          formulario.current?.reset();
          setNomeArquivo(null);
        },
      },
    );
  }

  return (
    <Secao titulo="Enviar PDF">
      <form ref={formulario} onSubmit={submeter} className="grid gap-5 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="block cursor-pointer">
          <span className="rotulo">Arquivo</span>
          <span className="block truncate border-b border-dashed border-fio py-2 text-apagado hover:border-acento">
            {nomeArquivo ?? "escolher PDF…"}
          </span>
          <input
            type="file"
            name="arquivo"
            accept="application/pdf"
            required
            className="sr-only"
            onChange={(e) => setNomeArquivo(e.target.files?.[0]?.name ?? null)}
          />
        </label>
        <Campo rotulo="Título (opcional)" name="titulo" maxLength={200} placeholder="nome do arquivo" />
        <Botao type="submit" carregando={enviar.isPending}>
          Enviar
        </Botao>
      </form>
      {grande && (
        <div role="alert" className="my-4 border-l-2 border-errado bg-alerta px-4 py-3 text-sm">
          {grande}
        </div>
      )}
      {enviar.isError && <Erro erro={enviar.error} />}
      <p className="mt-3 text-xs text-apagado">
        Até {LIMITE_UPLOAD_MB} MB. O texto é extraído, dividido em trechos e indexado para a busca. Leva alguns
        segundos por página.
      </p>
    </Secao>
  );
}
