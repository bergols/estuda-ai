"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

import { ErroApi } from "@/lib/api";

type Variante = "primario" | "secundario" | "discreto";

const VARIANTES: Record<Variante, string> = {
  // Botão principal "de tinta": fundo escuro sobre o papel (inverte no tema escuro)
  primario: "bg-tinta text-papel hover:bg-acento disabled:bg-apagado",
  secundario: "border border-tinta text-tinta hover:border-acento hover:text-acento",
  discreto: "text-apagado underline underline-offset-4 hover:text-acento",
};

export function Botao({
  variante = "primario",
  className = "",
  carregando = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variante?: Variante; carregando?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || carregando}
      aria-busy={carregando || undefined}
      className={`inline-flex items-center justify-center gap-2 rounded-sm text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
        variante === "discreto" ? "" : "min-h-10 px-4"
      } ${VARIANTES[variante]} ${className}`}
    >
      {children}
    </button>
  );
}

const CAMPO =
  "w-full border-0 border-b border-fio bg-transparent px-0 py-2 text-base text-tinta placeholder:text-apagado focus:border-acento focus:outline-none focus:ring-0";

export function Campo({
  rotulo,
  dica,
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { rotulo: string; dica?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="rotulo">{rotulo}</span>
      <input {...props} className={CAMPO} />
      {dica && <span className="mt-1 block text-xs text-apagado">{dica}</span>}
    </label>
  );
}

export function AreaTexto({
  rotulo,
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { rotulo: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="rotulo">{rotulo}</span>
      <textarea {...props} className={`${CAMPO} resize-y`} />
    </label>
  );
}

/** Título de página: serifa, com um fio embaixo (como o cabeçalho de uma folha). */
export function Cabecalho({
  titulo,
  sobre,
  acoes,
}: {
  titulo: ReactNode;
  sobre?: ReactNode;
  acoes?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3 border-b border-tinta pb-3">
      <div className="min-w-0">
        {sobre && <p className="rotulo mb-1">{sobre}</p>}
        <h1 className="font-serif text-3xl leading-tight font-semibold break-words">{titulo}</h1>
      </div>
      {acoes && <div className="flex items-center gap-3">{acoes}</div>}
    </header>
  );
}

export function Secao({ titulo, children, acoes }: { titulo: string; children: ReactNode; acoes?: ReactNode }) {
  return (
    <section className="mb-10">
      <div className="mb-3 flex items-baseline justify-between gap-3 border-b border-fio pb-1">
        <h2 className="rotulo">{titulo}</h2>
        {acoes}
      </div>
      {children}
    </section>
  );
}

// ------------------------------------------------- carregando / erro / vazio

export function Carregando({ texto = "Carregando" }: { texto?: string }) {
  return (
    <div role="status" aria-live="polite" className="py-8 text-sm text-apagado">
      <span className="inline-block animate-pulse">{texto}…</span>
    </div>
  );
}

function tempoAte(segundos: number): string {
  if (segundos < 90) return `${segundos} s`;
  if (segundos < 5400) return `${Math.ceil(segundos / 60)} min`;
  return `${Math.round(segundos / 3600)} h`;
}

export function textoDoErro(erro: unknown): string {
  if (erro instanceof ErroApi) {
    if (erro.status === 429 && erro.tentarDeNovoEmS) {
      return `${erro.message} (de novo em ${tempoAte(erro.tentarDeNovoEmS)})`;
    }
    if (erro.status >= 500) return "A API teve um problema. Tente de novo em instantes.";
    return erro.message;
  }
  return "Sem conexão com o servidor.";
}

export function Erro({ erro, tentarDeNovo }: { erro: unknown; tentarDeNovo?: () => void }) {
  return (
    <div role="alert" className="my-4 flex flex-wrap items-center justify-between gap-3 border-l-2 border-errado bg-alerta px-4 py-3 text-sm">
      <span>{textoDoErro(erro)}</span>
      {tentarDeNovo && (
        <Botao variante="discreto" onClick={tentarDeNovo}>
          tentar de novo
        </Botao>
      )}
    </div>
  );
}

export function Vazio({ children }: { children: ReactNode }) {
  return <div className="py-8 font-serif text-lg text-apagado italic">{children}</div>;
}
