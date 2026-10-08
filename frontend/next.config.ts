import type { NextConfig } from "next";

/**
 * Dois alvos, o mesmo código de telas:
 *
 * - web (padrão): servidor Node mínimo (.next/standalone/server.js) na Vercel ou em
 *   Docker, com o BFF (app/api/**) e o proxy.web.ts.
 * - desktop (ALVO=desktop): exportação estática em out/, empacotada pelo Tauri
 *   (desktop/). Não há servidor: quem fala com a API é o lado Rust do app.
 *
 * O que só existe na web termina em ".web.ts" (route.web.ts, proxy.web.ts). A web
 * reconhece essa extensão como página/rota; o desktop não, e o Next simplesmente
 * ignora esses arquivos no build estático, que não aceitaria rotas dinâmicas nem proxy.
 */
const desktop = process.env.ALVO === "desktop";

const nextConfig: NextConfig = {
  output: desktop ? "export" : "standalone",
  pageExtensions: desktop ? ["tsx", "ts"] : ["tsx", "ts", "web.ts"],
  // Pastas separadas: o "next dev" da web (BFF, porta 3000) e o do desktop (3001)
  // rodam juntos sem disputar o mesmo .next/. Na exportação, distDir também é a pasta
  // final dos arquivos estáticos: out/ é o que o Tauri empacota.
  distDir: desktop ? "out" : ".next",
  // pasta/index.html em vez de pasta.html: o servidor de arquivos do Tauri acha
  // "/login" sem depender de reescrever a extensão
  trailingSlash: desktop,
  // Tipos gerados das rotas ficam em <distDir>/types; cada alvo tem o seu tsconfig para
  // as declarações globais (LayoutProps, PageProps) de um não colidirem com as do outro
  typescript: { tsconfigPath: desktop ? "tsconfig.desktop.json" : "tsconfig.json" },
  env: {
    // Lido no navegador/webview (lib/plataforma.ts); fixado no build
    NEXT_PUBLIC_ALVO: desktop ? "desktop" : "web",
  },
  // Cache Components liga a pré-renderização parcial (PPR), que precisa de servidor para
  // completar a página na hora do pedido: a exportação estática recusa ("PPR cannot be
  // enabled in export mode"). No desktop, toda página já é casca estática + dados no cliente.
  cacheComponents: !desktop,
  partialPrefetching: !desktop,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Os links antigos (/disciplinas/5/flashcards) continuam funcionando na web.
  // Redirects precisam de servidor: não existem na exportação estática.
  ...(desktop
    ? {}
    : {
        async redirects() {
          return [
            { source: "/disciplinas/:id(\\d+)", destination: "/disciplina?id=:id", permanent: true },
            {
              source: "/disciplinas/:id(\\d+)/:aba(buscar|perguntar|flashcards|questoes)",
              destination: "/disciplina/:aba?id=:id",
              permanent: true,
            },
          ];
        },
      }),
};

export default nextConfig;
