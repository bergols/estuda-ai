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
  } catch (erro) {
    // Só no log do servidor (na Vercel: Logs do projeto), nunca na resposta ao navegador
    const causa = erro instanceof Error ? (erro.cause ?? erro) : erro;
    console.error(`BFF: falha ao chamar ${url.origin}:`, causa);
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

/**
 * Login na API (fluxo "password" do OAuth2: formulário username/password). Usado pelo
 * /api/sessao (web, grava cookie) e pelo /api/token (desktop, devolve o token).
 */
export async function loginNaApi(request: Request): Promise<Response> {
  const { email, senha } = (await request.json().catch(() => ({}))) as {
    email?: unknown;
    senha?: unknown;
  };
  if (typeof email !== "string" || typeof senha !== "string") {
    return Response.json({ detail: "informe e-mail e senha" }, { status: 422 });
  }
  return chamarApi("auth/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      ...cabecalhosDoBff(request),
    },
    body: new URLSearchParams({ username: email, password: senha }),
  });
}

/** Erro da API repassado só com a mensagem e o Retry-After (429 do rate limit). */
export async function erroDaApi(resposta: Response): Promise<Response> {
  const corpo = await resposta.json().catch(() => ({ detail: "erro na API" }));
  const cabecalhos: Record<string, string> = {};
  const retry = resposta.headers.get("retry-after");
  if (retry) cabecalhos["Retry-After"] = retry;
  return Response.json({ detail: corpo.detail }, { status: resposta.status, headers: cabecalhos });
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
