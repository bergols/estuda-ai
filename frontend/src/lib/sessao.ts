import "server-only";

import { ipDoCliente, origemConfiavel as origemPermitida } from "./bff";

/**
 * Sessão no servidor do Next (BFF, "backend for frontend").
 *
 * O JWT da API fica num cookie httpOnly: o JavaScript da página NÃO consegue lê-lo
 * (document.cookie não mostra), então um script injetado (XSS) não rouba o login.
 * O navegador só conversa com o Next; o Next lê o cookie e chama a API com
 * "Authorization: Bearer". O token nunca chega ao código do cliente.
 */

export const COOKIE_SESSAO = "estuda_ai_sessao";

export class ErroDeConfiguracao extends Error {}

export function urlDaApi(caminho: string): URL {
  const base = process.env.BACKEND_URL;
  if (!base) throw new ErroDeConfiguracao("BACKEND_URL não configurada no servidor do frontend");
  return new URL(caminho, base.endsWith("/") ? base : `${base}/`);
}

/**
 * fetch para a API com falhas traduzidas em respostas claras, em vez de um 500 genérico:
 * - variável faltando no deploy (ex.: BACKEND_URL salva depois do último deploy): 500
 *   dizendo qual;
 * - API inalcançável (servidor desligado, túnel caído, DNS): 503 "fora do ar".
 */
export async function chamarApi(caminho: string, init: RequestInit): Promise<Response> {
  let url: URL;
  try {
    url = urlDaApi(caminho);
  } catch (erro) {
    if (erro instanceof ErroDeConfiguracao) {
      return Response.json({ detail: erro.message }, { status: 500 });
    }
    throw erro;
  }
  try {
    return await fetch(url, { ...init, cache: "no-store", signal: AbortSignal.timeout(120_000) });
  } catch {
    return Response.json(
      { detail: "o servidor da API está fora do ar no momento; tente de novo mais tarde" },
      { status: 503 },
    );
  }
}

/**
 * Cabeçalhos que provam à API que a requisição veio do BFF e qual é o IP real do
 * cliente (a API vê o IP do servidor do Next). Sem o segredo, a API ignora o IP.
 */
export function cabecalhosDoBff(request: Request): Record<string, string> {
  const cabecalhos: Record<string, string> = {};
  const segredo = process.env.BFF_SEGREDO;
  const ip = ipDoCliente(request.headers.get("x-forwarded-for"));
  if (segredo && ip) {
    cabecalhos["X-BFF-Segredo"] = segredo;
    cabecalhos["X-Cliente-IP"] = ip;
  }
  return cabecalhos;
}

export function opcoesDoCookie(expiraEm: Date) {
  return {
    httpOnly: true, // invisível para o JavaScript da página
    // Só por HTTPS em produção (em http://localhost o navegador recusaria o cookie)
    secure: process.env.NODE_ENV === "production",
    // Lax: o navegador NÃO envia o cookie em POST/PATCH/DELETE vindos de outro site
    // (proteção contra CSRF), mas envia ao seguir um link para cá.
    sameSite: "lax" as const,
    path: "/",
    expires: expiraEm,
  };
}

/** CSRF: ver origemConfiavel em bff.ts. */
export function origemConfiavel(request: Request): boolean {
  return origemPermitida(request.method, request.headers.get("sec-fetch-site"));
}
