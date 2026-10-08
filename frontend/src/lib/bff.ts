/**
 * Regras de segurança do BFF como funções puras (sem Next, sem cookies), para
 * serem testadas isoladamente em bff.test.ts. As rotas em app/api/ só as aplicam.
 */

/** Primeiro segmento permitido no repasse /api/<caminho> → API. */
const PERMITIDOS = new Set(["disciplinas", "revisoes", "analytics", "gastos"]);
/** Em /auth, só estes: o login tem rota própria (/api/sessao), que grava o cookie. */
const AUTH_PERMITIDOS = new Set(["eu", "sair-de-todos"]);

/**
 * O BFF não é um proxy aberto: só repassa caminhos da allowlist.
 *
 * Segmentos "." e ".." (e vazios) são recusados em QUALQUER posição. Conferir só o
 * primeiro segmento não basta: "disciplinas/../docs" passaria pela allowlist e a URL
 * resolveria para /docs na API. E codificar não resolve: pelo padrão WHATWG de URL,
 * "%2E%2E" também é ".." (o teste em bff.test.ts mostrou isso).
 */
export function caminhoPermitido(partes: string[]): boolean {
  if (partes.length === 0) return false;
  if (partes.some((p) => p === "" || p === "." || p === "..")) return false;
  const [primeiro, segundo] = partes;
  if (primeiro === "auth") return partes.length === 2 && AUTH_PERMITIDOS.has(segundo);
  return PERMITIDOS.has(primeiro);
}

/**
 * Caminho para a URL da API (só para partes já aprovadas por caminhoPermitido).
 * Cada parte é recodificada: um "/" dentro de um segmento vira "%2F", não separador.
 */
export function caminhoDaApi(partes: string[]): string {
  return partes.map(encodeURIComponent).join("/");
}

/**
 * Defesa contra CSRF além do SameSite=Lax: navegadores modernos mandam
 * Sec-Fetch-Site em toda requisição; mutação vinda de outro site é recusada.
 * Sem o header (navegador antigo, curl), passa: o cookie SameSite ainda protege.
 */
export function origemConfiavel(metodo: string, secFetchSite: string | null): boolean {
  if (["GET", "HEAD"].includes(metodo.toUpperCase())) return true;
  return secFetchSite === null || secFetchSite === "same-origin";
}

/**
 * IP do cliente a partir de X-Forwarded-For ("cliente, proxy1, proxy2"): o 1o é o
 * cliente, escrito pela plataforma de hospedagem na borda. A API ainda valida o
 * formato e só confia nele com o segredo do BFF.
 */
export function ipDoCliente(xForwardedFor: string | null): string | null {
  const primeiro = xForwardedFor?.split(",")[0]?.trim();
  return primeiro ? primeiro : null;
}
