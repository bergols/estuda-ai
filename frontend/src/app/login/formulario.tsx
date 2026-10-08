"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Botao, Campo, Erro } from "@/components/ui";
import { ErroApi } from "@/lib/api";
import { entrarPelaPonte } from "@/lib/desktop";
import { DESKTOP } from "@/lib/plataforma";

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
      const email = String(form.get("email"));
      const senha = String(form.get("senha"));
      // Web: o BFF (/api/sessao) grava o cookie httpOnly. Desktop: o Rust guarda o
      // token no cofre do sistema. Nos dois, o token não volta para a página.
      const resposta = DESKTOP
        ? await entrarPelaPonte(email, senha)
        : await fetch("/api/sessao", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, senha }),
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
