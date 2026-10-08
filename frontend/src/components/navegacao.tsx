"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { BotaoTema } from "./tema";

const ITENS = [
  { href: "/", rotulo: "Disciplinas", ativo: (p: string) => p === "/" || p.startsWith("/disciplinas") },
  { href: "/revisao", rotulo: "Revisão", ativo: (p: string) => p.startsWith("/revisao") },
  { href: "/painel", rotulo: "Painel", ativo: (p: string) => p.startsWith("/painel") },
  { href: "/conta", rotulo: "Conta", ativo: (p: string) => p.startsWith("/conta") },
];

function Links({ caminho }: { caminho: string | null }) {
  return ITENS.map((item) => {
    const ativo = caminho !== null && item.ativo(caminho);
    return (
      <Link
        key={item.href}
        href={item.href}
        aria-current={ativo ? "page" : undefined}
        className={`text-sm ${ativo ? "text-tinta underline decoration-acento decoration-2 underline-offset-8" : "text-apagado hover:text-tinta"}`}
      >
        {item.rotulo}
      </Link>
    );
  });
}

function LinksComDestaque() {
  return <Links caminho={usePathname()} />;
}

/**
 * O caminho atual (usePathname) só existe na hora da requisição. Com Cache Components,
 * quem lê a URL fica dentro de um <Suspense>: o resto da página é pré-renderizado
 * (aparece na hora) e só o destaque do link ativo chega depois. O fallback são os
 * mesmos links, sem destaque, para nada pular de lugar.
 */
function LinksDaNavegacao() {
  return (
    <Suspense fallback={<Links caminho={null} />}>
      <LinksComDestaque />
    </Suspense>
  );
}

/** No computador: barra no topo. No celular: barra fixa embaixo (alcance do polegar). */
export function Navegacao() {
  return (
    <>
      <header className="border-b border-fio">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-6 px-4 py-3">
          <Link href="/" className="font-serif text-xl font-semibold tracking-tight">
            estuda<span className="text-acento">·</span>ai
          </Link>
          <nav aria-label="Principal" className="hidden items-center gap-6 sm:flex">
            <LinksDaNavegacao />
          </nav>
          <BotaoTema />
        </div>
      </header>
      <nav
        aria-label="Principal (celular)"
        className="fixed inset-x-0 bottom-0 z-10 flex justify-around border-t border-fio bg-papel px-2 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:hidden"
      >
        <LinksDaNavegacao />
      </nav>
    </>
  );
}
