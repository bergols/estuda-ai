"use client";

import type { ReactNode } from "react";

import type { Ordem } from "@/lib/consultas";
import type { Grupo } from "@/lib/ordem-conteudo";

/** "ordem da matéria" | "mais recentes" (texto discreto; o ativo fica em tinta). */
export function SeletorOrdem({ ordem, mudar }: { ordem: Ordem; mudar: (o: Ordem) => void }) {
  const opcao = (valor: Ordem, rotulo: string) => (
    <button
      type="button"
      onClick={() => mudar(valor)}
      aria-pressed={ordem === valor}
      className={ordem === valor ? "text-tinta" : "text-apagado underline underline-offset-4 hover:text-acento"}
    >
      {rotulo}
    </button>
  );
  return (
    <span className="flex gap-3 text-sm">
      {opcao("conteudo", "ordem da matéria")}
      {opcao("recentes", "mais recentes")}
    </span>
  );
}

/** Lista na ordem da matéria: um título por material e um rótulo por tópico. */
export function ListaAgrupada<T>({
  grupos,
  numerada = false,
  item,
}: {
  grupos: Grupo<T>[];
  numerada?: boolean;
  item: (item: T, numero: number) => ReactNode;
}) {
  const Lista = numerada ? "ol" : "ul";
  return (
    <>
      {grupos.map((g, i) => (
        <section key={i} className="mt-8 first:mt-2">
          <h3 className="border-b border-tinta pb-1 font-serif text-xl">{g.material ?? "Sem material de origem"}</h3>
          {g.topicos.map((t, j) => (
            <div key={j}>
              <p className="rotulo mt-4">{t.topico ?? "Sem tópico"}</p>
              <Lista className="divide-y divide-fio">{t.itens.map(({ item: x, numero }) => item(x, numero))}</Lista>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
