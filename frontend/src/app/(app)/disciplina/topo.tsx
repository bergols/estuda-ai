"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { Cabecalho, Erro } from "@/components/ui";
import { useDisciplina } from "@/lib/consultas";

const ABAS = [
  { sufixo: "", rotulo: "Materiais" },
  { sufixo: "/buscar", rotulo: "Buscar" },
  { sufixo: "/perguntar", rotulo: "Perguntar" },
  { sufixo: "/flashcards", rotulo: "Flashcards" },
  { sufixo: "/questoes", rotulo: "Questões" },
];

/**
 * O id vem da query string (/disciplina/flashcards?id=5), não de um segmento dinâmico
 * (/disciplinas/5/flashcards): o app desktop usa a exportação estática do Next, que só
 * gera páginas conhecidas no build, e os ids das disciplinas só existem no banco.
 * Ver docs/modo-foco.md, "Por que exportação estática".
 */
export function useDisciplinaId(): number {
  return Number(useSearchParams().get("id"));
}

export function TopoDisciplina() {
  const id = useDisciplinaId();
  const caminho = usePathname();
  const disciplina = useDisciplina(id);

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
          const pagina = `/disciplina${aba.sufixo}`;
          const href = `${pagina}?id=${id}`;
          const ativa = caminho === pagina;
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
