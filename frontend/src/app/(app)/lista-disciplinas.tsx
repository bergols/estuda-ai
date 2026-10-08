"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { Botao, Cabecalho, Campo, Carregando, Erro, Vazio } from "@/components/ui";
import { useCriarDisciplina, useDisciplinas, useEu } from "@/lib/consultas";

export function ListaDisciplinas() {
  const eu = useEu();
  const disciplinas = useDisciplinas();
  const [criando, setCriando] = useState(false);

  return (
    <>
      <Cabecalho
        sobre={eu.data ? `Olá, ${eu.data.nome.split(" ")[0]}` : "Sumário"}
        titulo="Disciplinas"
        acoes={
          !criando && (
            <Botao variante="secundario" onClick={() => setCriando(true)}>
              Nova disciplina
            </Botao>
          )
        }
      />
      {criando && <NovaDisciplina fechar={() => setCriando(false)} />}

      {disciplinas.isPending && <Carregando />}
      {disciplinas.isError && <Erro erro={disciplinas.error} tentarDeNovo={() => disciplinas.refetch()} />}
      {disciplinas.data?.length === 0 && !criando && (
        <Vazio>Nenhuma disciplina ainda. Crie a primeira para enviar materiais.</Vazio>
      )}

      {/* Lista como o sumário de um caderno: linhas separadas por fios, sem cartões */}
      <ol className="divide-y divide-fio">
        {disciplinas.data?.map((d, i) => (
          <li key={d.id}>
            <Link
              href={`/disciplina?id=${d.id}`}
              className="group grid grid-cols-[2.5rem_1fr_auto] items-baseline gap-x-3 py-4"
            >
              <span className="font-mono text-sm text-apagado">{String(i + 1).padStart(2, "0")}</span>
              <span className="min-w-0">
                <span className="block font-serif text-xl group-hover:text-acento">{d.nome}</span>
                {d.descricao && <span className="block truncate text-sm text-apagado">{d.descricao}</span>}
              </span>
              <span aria-hidden className="text-apagado group-hover:text-acento">→</span>
            </Link>
          </li>
        ))}
      </ol>
    </>
  );
}

function NovaDisciplina({ fechar }: { fechar: () => void }) {
  const criar = useCriarDisciplina();

  function enviar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const form = new FormData(evento.currentTarget);
    const descricao = String(form.get("descricao") ?? "").trim();
    criar.mutate(
      { nome: String(form.get("nome")), descricao: descricao || null },
      { onSuccess: fechar },
    );
  }

  return (
    <form onSubmit={enviar} className="mb-8 space-y-5 bg-tarja p-5">
      <Campo rotulo="Nome" name="nome" required maxLength={120} autoFocus placeholder="Banco de Dados" />
      <Campo rotulo="Descrição (opcional)" name="descricao" maxLength={500} placeholder="Prof., semestre, ementa…" />
      {criar.isError && <Erro erro={criar.error} />}
      <div className="flex gap-4">
        <Botao type="submit" carregando={criar.isPending}>
          Criar
        </Botao>
        <Botao type="button" variante="discreto" onClick={fechar}>
          cancelar
        </Botao>
      </div>
    </form>
  );
}
