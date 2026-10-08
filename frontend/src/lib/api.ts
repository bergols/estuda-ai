import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./api-schema";
import { fetchPelaPonte } from "./desktop";
import { DESKTOP } from "./plataforma";

/**
 * Cliente tipado da API, gerado do OpenAPI do backend (npm run tipos). Caminhos,
 * parâmetros e respostas são conferidos pelo TypeScript: se o backend mudar uma rota
 * ou um campo, o build do frontend quebra em vez de o erro aparecer só em produção.
 *
 * Tudo passa pelo BFF (/api/...), que acrescenta o JWT a partir do cookie httpOnly.
 * No app desktop, o mesmo cliente usa a ponte para o Rust (lib/desktop.ts), que
 * acrescenta o JWT guardado no cofre do sistema.
 */
export type Esquemas = components["schemas"];

export class ErroApi extends Error {
  constructor(
    public status: number,
    mensagem: string,
    public tentarDeNovoEmS?: number,
  ) {
    super(mensagem);
  }
}

const sessaoExpirada: Middleware = {
  onResponse({ response }) {
    // 401: token expirado ou revogado. O BFF já apagou o cookie; volta ao login.
    if (response.status === 401 && typeof window !== "undefined") {
      // Recarga completa de propósito: zera o cache do TanStack Query do usuário anterior.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login");
    }
  },
};

export const api = createClient<paths>({
  baseUrl: "/api",
  fetch: DESKTOP ? fetchPelaPonte : undefined,
});
api.use(sessaoExpirada);

/** A API devolve {"detail": "mensagem"} (ou a lista de erros de validação). */
function mensagemDoErro(erro: unknown): string {
  const detalhe = (erro as { detail?: unknown } | undefined)?.detail;
  if (typeof detalhe === "string") return detalhe;
  if (Array.isArray(detalhe)) {
    return detalhe.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ");
  }
  // Falha do LLM (502): {"mensagem": "...", "detalhe": "...", "geracao_id": ...}
  if (detalhe && typeof detalhe === "object" && "mensagem" in detalhe) {
    const { mensagem, detalhe: motivo } = detalhe as { mensagem: string; detalhe?: string };
    return motivo ? `${mensagem}: ${motivo}` : mensagem;
  }
  return "algo deu errado; tente de novo";
}

/** Desembrulha o resultado do openapi-fetch: devolve os dados ou lança ErroApi. */
export async function dados<T>(
  chamada: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await chamada;
  if (error !== undefined || !response.ok) {
    const retry = Number(response.headers.get("retry-after")) || undefined;
    throw new ErroApi(response.status, mensagemDoErro(error), retry);
  }
  return data as T;
}
