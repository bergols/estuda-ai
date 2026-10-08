"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ErroApi } from "@/lib/api";

export function Provedores({ children }: { children: React.ReactNode }) {
  // Um QueryClient por aba (useState): criado no módulo, ele seria compartilhado
  // entre requisições no servidor.
  const [cliente] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            // Erro do cliente (404, 422, 429...) não melhora tentando de novo
            retry: (tentativas, erro) =>
              !(erro instanceof ErroApi && erro.status < 500) && tentativas < 2,
          },
        },
      }),
  );
  return <QueryClientProvider client={cliente}>{children}</QueryClientProvider>;
}
