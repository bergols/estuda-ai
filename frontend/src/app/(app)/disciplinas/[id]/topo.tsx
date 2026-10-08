"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

import { Cabecalho, Erro } from "@/components/ui";
import { useDisciplina } from "@/lib/consultas";

const ABAS = [
  { sufixo: "", rotulo: "Materiais" },
  { sufixo: "/buscar", rotulo: "Buscar" },
  { sufixo: "/perguntar", rotulo: "Perguntar" },
  { sufixo: "/flashcards", rotulo: "Flashcards" },
  { sufixo: "/questoes", rotulo: "Questões" },
];

export function useDisciplinaId(): number {
  return Number(useParams<{ id: string }>().id);
}

export function TopoDisciplina() {
  const id = useDisciplinaId();
  const caminho = usePathname();
  const disciplina = useDisciplina(id);
  const base = `/disciplinas/${id}`;

  if (disciplina.isError) return <Erro erro={disciplina.error} />;
  return (
    <>
      <Cabecalho
        sobre={<Link href="/" className="hover:text-acento">← Disciplinas</Link>}
        titulo={disciplina.data?.nome ?? " "}
      />
      {/* Abas como texto sublinhado (rolam na horizontal no celular) */}
      <nav aria-label="Seções da disciplina" className="-mt-3 mb-8 flex gap-6 overflow-x-auto border-b border-fio [scrollbar-width:none]">
        {ABAS.map((aba) => {
          const href = base + aba.sufixo;
          const ativa = caminho === href;
          return (
            <Link
              key={aba.rotulo}
              href={href}
              aria-current={ativa ? "page" : undefined}
              className={`shrink-0 border-b-2 py-2 text-sm ${
                ativa ? "border-acento text-tinta" : "border-transparent text-apagado hover:text-tinta"
              }`}
            >
              {aba.rotulo}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
