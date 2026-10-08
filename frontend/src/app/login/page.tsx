import type { Metadata } from "next";

import { FormularioLogin } from "./formulario";

export const metadata: Metadata = { title: "Entrar" };

export default function PaginaLogin() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 py-12">
      <p className="font-serif text-4xl font-semibold tracking-tight">
        estuda<span className="text-acento">·</span>ai
      </p>
      <p className="mt-2 mb-10 text-apagado">Seus materiais, flashcards e revisões num lugar só.</p>
      <FormularioLogin />
      <p className="mt-10 border-t border-fio pt-4 text-xs text-apagado">
        Não há cadastro público. A conta é criada pelo administrador.
      </p>
    </main>
  );
}
