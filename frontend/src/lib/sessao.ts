import "server-only";

/**
 * Sessão no servidor do Next (BFF, "backend for frontend").
 *
 * O JWT da API fica num cookie httpOnly: o JavaScript da página NÃO consegue lê-lo
 * (document.cookie não mostra), então um script injetado (XSS) não rouba o login.
 * O navegador só conversa com o Next; o Next lê o cookie e chama a API com
 * "Authorization: Bearer". O token nunca chega ao código do cliente.
 */

export const COOKIE_SESSAO = "estuda_ai_sessao";

export function urlDaApi(caminho: string): URL {
  const base = process.env.BACKEND_URL;
  if (!base) throw new Error("BACKEND_URL não configurada");
  return new URL(caminho, base.endsWith("/") ? base : `${base}/`);
}

/**
 * Cabeçalhos que provam à API que a requisição veio do BFF e qual é o IP real do
 * cliente (a API vê o IP do servidor do Next). Sem o segredo, a API ignora o IP.
 */
export function cabecalhosDoBff(request: Request): Record<string, string> {
  const cabecalhos: Record<string, string> = {};
  const segredo = process.env.BFF_SEGREDO;
  // x-forwarded-for: "cliente, proxy1, proxy2". O 1o é o cliente, escrito pela
  // plataforma de hospedagem na borda (um valor enviado pelo navegador é substituído).
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
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

/**
 * Defesa extra contra CSRF, além do SameSite: navegadores modernos mandam
 * Sec-Fetch-Site em toda requisição. Mutação vinda de outro site é recusada.
 */
export function origemConfiavel(request: Request): boolean {
  if (["GET", "HEAD"].includes(request.method)) return true;
  const site = request.headers.get("sec-fetch-site");
  return site === null || site === "same-origin";
}
