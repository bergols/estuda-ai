"use client";

import { useEffect, useState } from "react";

import { Botao } from "@/components/ui";
import {
  INTERVALO_VERIFICACAO_MS,
  type Novidade,
  instalarAtualizacao,
  quandoInstalar,
  verificarAtualizacao,
} from "@/lib/atualizacao";
import { sessaoEmAndamento } from "@/lib/foco/local";

type Estado =
  | { fase: "nada" }
  | { fase: "aviso" | "instalando"; novidade: Novidade }
  | { fase: "erro"; novidade: Novidade; mensagem: string };

/**
 * Procura versão nova ao abrir o app e a cada 6 h (só no desktop). As regras de quando
 * instalar estão em lib/atualizacao.ts; sem internet, não mostra nada e tenta de novo
 * no próximo intervalo.
 */
export function Atualizacao() {
  const [estado, setEstado] = useState<Estado>({ fase: "nada" });

  function instalar(novidade: Novidade) {
    setEstado({ fase: "instalando", novidade });
    instalarAtualizacao().catch((e) => setEstado({ fase: "erro", novidade, mensagem: String(e) }));
  }

  useEffect(() => {
    let cancelado = false;
    const verificar = async () => {
      try {
        const novidade = await verificarAtualizacao();
        if (!novidade || cancelado) return;
        const sessao = await sessaoEmAndamento().catch(() => null);
        // performance.now(): ms desde que a página (o app) abriu
        if (quandoInstalar({ desdeAbertura: performance.now(), sessaoAberta: !!sessao }) === "agora") {
          instalar(novidade);
        } else {
          setEstado((atual) => (atual.fase === "instalando" ? atual : { fase: "aviso", novidade }));
        }
      } catch {
        // Sem internet ou GitHub fora do ar: nada a mostrar, tenta no próximo intervalo
      }
    };
    verificar();
    const id = setInterval(verificar, INTERVALO_VERIFICACAO_MS);
    return () => {
      cancelado = true;
      clearInterval(id);
    };
  }, []);

  if (estado.fase === "nada") return null;

  if (estado.fase === "instalando") {
    return (
      <div role="status" className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-3 bg-papel px-4 text-center">
        <p className="font-serif text-2xl">Atualizando para a versão {estado.novidade.versao}</p>
        <p className="max-w-md text-sm text-apagado">
          O estuda-ai baixa a versão nova, confere a assinatura e reinicia sozinho. Leva alguns segundos.
        </p>
      </div>
    );
  }

  return (
    <div role="status" className="fixed inset-x-0 top-0 z-40 border-b border-fio bg-papel px-4 py-2 text-sm">
      <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex-1">
          {estado.fase === "erro"
            ? `Não consegui instalar a versão ${estado.novidade.versao}: ${estado.mensagem}`
            : `Versão ${estado.novidade.versao} disponível.`}
        </span>
        <Botao variante="discreto" onClick={() => instalar(estado.novidade)}>
          {estado.fase === "erro" ? "tentar de novo" : "reiniciar e atualizar"}
        </Botao>
        <Botao variante="discreto" onClick={() => setEstado({ fase: "nada" })}>
          depois
        </Botao>
      </div>
    </div>
  );
}
