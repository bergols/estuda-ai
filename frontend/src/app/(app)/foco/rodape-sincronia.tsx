import type { SituacaoSincronia } from "@/lib/foco/local";

/** "Sincronizado" ou "2 sessões aguardando conexão": o modo offline visível. */
export function RodapeSincronia({ situacao, className = "" }: { situacao: SituacaoSincronia | null; className?: string }) {
  if (!situacao) return null;
  const { pendentes, recusadas, erro } = situacao;
  let texto = "Sessões sincronizadas";
  if (pendentes > 0) {
    texto = `${pendentes} ${pendentes === 1 ? "sessão aguardando" : "sessões aguardando"} conexão`;
    if (erro) texto += ` (${erro})`;
  }
  return (
    <p className={`text-xs text-apagado ${className}`} role="status" aria-live="polite">
      <span aria-hidden className={`mr-1.5 inline-block size-1.5 rounded-full align-middle ${pendentes > 0 ? "bg-acento" : "bg-certo"}`} />
      {texto}
      {recusadas > 0 && ` · ${recusadas} recusada${recusadas > 1 ? "s" : ""} pelo servidor`}
    </p>
  );
}
