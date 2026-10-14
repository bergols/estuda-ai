"use client";

import { useQueryClient } from "@tanstack/react-query";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useEffectEvent, useState } from "react";

import { Botao } from "@/components/ui";

type Estado =
  | { fase: "nada" }
  | { fase: "ligando"; etapa: string; desde: number }
  | { fase: "erro"; mensagem: string };

/**
 * No Mac que hospeda a API (desktop/src-tauri/src/servidor.rs): ao abrir o app, liga a
 * VM e o compose se estiverem desligados e cobre a tela até o /health responder. Em
 * qualquer outro computador o Rust responde "não gerenciado" e nada aparece.
 */
export function ServidorLocal() {
  const cliente = useQueryClient();
  const [estado, setEstado] = useState<Estado>({ fase: "nada" });
  const [agora, setAgora] = useState(() => Date.now());

  async function ligar() {
    setEstado({ fase: "ligando", etapa: "conferindo o servidor", desde: Date.now() });
    try {
      await invoke("servidor_ligar");
      setEstado({ fase: "nada" });
      // O que a tela tentou buscar enquanto a API estava desligada: de novo
      await cliente.invalidateQueries();
    } catch (e) {
      setEstado({ fase: "erro", mensagem: String(e) });
    }
  }

  const ligarNaAbertura = useEffectEvent(() => {
    void ligar();
  });

  useEffect(() => {
    let vivo = true;
    const parar = listen<string>("servidor-etapa", (e) =>
      setEstado((atual) => (atual.fase === "ligando" ? { ...atual, etapa: e.payload } : atual)),
    );
    invoke<{ gerenciado: boolean; saudavel: boolean }>("servidor_situacao").then(
      (s) => {
        if (vivo && s.gerenciado && !s.saudavel) ligarNaAbertura();
      },
      () => {},
    );
    return () => {
      vivo = false;
      parar.then((f) => f(), () => {});
    };
  }, []);

  // Relógio da tela "iniciando" (religar tudo leva ~25 s)
  useEffect(() => {
    if (estado.fase !== "ligando") return;
    const id = setInterval(() => setAgora(Date.now()), 1000);
    return () => clearInterval(id);
  }, [estado.fase]);

  if (estado.fase === "nada") return null;
  return (
    <div role="status" className="fixed inset-0 z-[55] flex flex-col items-center justify-center gap-3 bg-papel px-4 text-center">
      {estado.fase === "ligando" ? (
        <>
          <p className="font-serif text-2xl">Iniciando o servidor…</p>
          <p className="text-sm text-apagado">
            {estado.etapa} · <span className="font-mono tabular-nums">{Math.max(0, Math.round((agora - estado.desde) / 1000))} s</span>
          </p>
          <p className="max-w-md text-xs text-apagado">
            A API roda neste Mac e liga junto com o app (leva uns 25 s). Ao fechar o app, ela desliga sozinha.
          </p>
        </>
      ) : (
        <>
          <p className="font-serif text-2xl">Não consegui ligar o servidor</p>
          <p className="max-w-md text-sm text-apagado">{estado.mensagem}</p>
          <div className="flex gap-4">
            <Botao onClick={ligar}>Tentar de novo</Botao>
            <Botao variante="discreto" onClick={() => setEstado({ fase: "nada" })}>
              continuar sem servidor
            </Botao>
          </div>
        </>
      )}
    </div>
  );
}
