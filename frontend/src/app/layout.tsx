import type { Metadata, Viewport } from "next";
import { Source_Serif_4 } from "next/font/google";

import { Provedores } from "./provedores";
import "katex/dist/katex.min.css"; // fórmulas (components/texto-rico.tsx)
import "./globals.css";

const serifa = Source_Serif_4({ variable: "--fonte-serifa", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "estuda-ai", template: "%s · estuda-ai" },
  description: "Assistente de estudos: materiais, busca, flashcards, questões e revisão espaçada.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf8f4" },
    { media: "(prefers-color-scheme: dark)", color: "#1d1b18" },
  ],
};

// Roda ANTES da primeira pintura: aplica o tema salvo, sem o "flash" de tema errado
// que um useEffect causaria (a página apareceria clara e depois escureceria).
const SCRIPT_TEMA = `try{var t=localStorage.getItem("tema");if(t==="claro"||t==="escuro")document.documentElement.dataset.tema=t}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: o script acima muda data-tema antes do React hidratar
    <html lang="pt-BR" className={`${serifa.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_TEMA }} />
      </head>
      <body className="min-h-full">
        <Provedores>{children}</Provedores>
      </body>
    </html>
  );
}
