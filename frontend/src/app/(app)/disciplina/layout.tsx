import { Suspense } from "react";

import { Carregando } from "@/components/ui";

import { TopoDisciplina } from "./topo";

/**
 * O id da disciplina vem da URL (?id=), que só existe na hora da requisição:
 * topo e conteúdo ficam em <Suspense>, com um "esqueleto" do mesmo tamanho no lugar.
 */
export default function LayoutDisciplina({ children }: LayoutProps<"/disciplina">) {
  return (
    <>
      <Suspense fallback={<div className="mb-8 h-[7.5rem] border-b border-fio" aria-hidden />}>
        <TopoDisciplina />
      </Suspense>
      <Suspense fallback={<Carregando />}>{children}</Suspense>
    </>
  );
}
