"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Botao, Campo, Erro } from "@/components/ui";
import { ErroApi } from "@/lib/api";

export function FormularioLogin() {
  const router = useRouter();
  const [erro, setErro] = useState<unknown>(null);
  const [enviando, setEnviando] = useState(false);

  async function entrar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const form = new FormData(evento.currentTarget);
    setEnviando(true);
    setErro(null);
    try {
      // Vai para o BFF (/api/sessao), que grava o cookie httpOnly. O token não volta.
      const resposta = await fetch("/api/sessao", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: form.get("email"), senha: form.get("senha") }),
      });
      if (!resposta.ok) {
        const corpo = await resposta.json().catch(() => ({}));
        const retry = Number(resposta.headers.get("retry-after")) || undefined;
        throw new ErroApi(resposta.status, corpo.detail ?? "não foi possível entrar", retry);
      }
      router.replace("/");
      router.refresh();
    } catch (e) {
      setErro(e);
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={entrar} className="space-y-6">
      <Campo rotulo="E-mail" name="email" type="email" autoComplete="username" required autoFocus />
      <Campo rotulo="Senha" name="senha" type="password" autoComplete="current-password" required minLength={8} />
      {erro !== null && <Erro erro={erro} />}
      <Botao type="submit" carregando={enviando} className="w-full">
        {enviando ? "Entrando…" : "Entrar"}
      </Botao>
    </form>
  );
}
