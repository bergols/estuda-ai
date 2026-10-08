import { invoke } from "@tauri-apps/api/core";

/**
 * Ponte para o lado Rust do app desktop (desktop/src-tauri/src/ponte.rs).
 *
 * Na web, a página chama /api/... e o BFF acrescenta o token a partir do cookie
 * httpOnly. No desktop não há servidor Next: a página entrega o pedido ao Rust, que
 * lê o token do cofre do sistema e chama o BFF com "Authorization: Bearer". Nos dois
 * casos o token nunca passa pelo JavaScript.
 */

type RespostaPonte = {
  status: number;
  tipo: string | null;
  retry_after: string | null;
  corpo: string;
};

/** Status que, pela especificação do fetch, não podem ter corpo. */
const SEM_CORPO = new Set([101, 204, 205, 304]);

export function respostaDaPonte(r: RespostaPonte): Response {
  const cabecalhos = new Headers();
  if (r.tipo) cabecalhos.set("content-type", r.tipo);
  if (r.retry_after) cabecalhos.set("retry-after", r.retry_after);
  return new Response(SEM_CORPO.has(r.status) ? null : r.corpo, { status: r.status, headers: cabecalhos });
}

/** "/api/disciplinas/5?q=x" → "disciplinas/5?q=x" (o que o Rust espera). */
export function caminhoDaPonte(url: URL): string {
  return url.pathname.replace(/^\/api\//, "") + url.search;
}

/**
 * `fetch` com a mesma assinatura do navegador, para o openapi-fetch usar sem saber
 * que existe uma ponte. O corpo vai em bytes (o upload de PDF em multipart passa
 * intacto, com o boundary no content-type).
 */
export async function fetchPelaPonte(entrada: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const pedido = new Request(entrada, init);
  const temCorpo = !["GET", "HEAD"].includes(pedido.method);
  const corpo = temCorpo ? new Uint8Array(await pedido.arrayBuffer()) : new Uint8Array();
  const resposta = await invoke<RespostaPonte>("chamar_api", corpo, {
    headers: {
      "x-metodo": pedido.method,
      "x-caminho": caminhoDaPonte(new URL(pedido.url)),
      "content-type": pedido.headers.get("content-type") ?? "",
    },
  });
  return respostaDaPonte(resposta);
}

/** Login: o Rust troca e-mail e senha pelo token e o guarda no cofre do sistema. */
export async function entrarPelaPonte(email: string, senha: string): Promise<Response> {
  return respostaDaPonte(await invoke<RespostaPonte>("entrar", { email, senha }));
}

/** Sair deste computador: apaga o token do cofre. */
export async function sairPelaPonte(): Promise<void> {
  await invoke("sair");
}
