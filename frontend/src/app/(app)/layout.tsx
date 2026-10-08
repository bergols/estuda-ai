import { Navegacao } from "@/components/navegacao";

export default function LayoutDoApp({ children }: LayoutProps<"/">) {
  return (
    <>
      <Navegacao />
      {/* pb-24 no celular: espaço para a barra de navegação fixa embaixo */}
      <main className="mx-auto max-w-4xl px-4 pt-8 pb-24 sm:pb-12">{children}</main>
    </>
  );
}
