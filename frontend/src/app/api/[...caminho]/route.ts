import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { COOKIE_SESSAO, cabecalhosDoBff, origemConfiavel, urlDaApi } from "@/lib/sessao";

/**
 * Repassa /api/<caminho> para a API, com o JWT do cookie em "Authorization".
 *
 * Só caminhos da allowlist passam: o BFF não é um proxy aberto para qualquer rota da
 * API (nem para outro host). /auth/login tem rota própria (/api/sessao), que grava o
 * cookie; aqui ele não está na lista.
 */
const PERMITIDOS = new Set(["disciplinas", "revisoes", "analytics", "gastos"]);
const AUTH_PERMITIDOS = new Set(["eu", "sair-de-todos"]);

function caminhoPermitido(partes: string[]): boolean {
  const [primeiro, segundo] = partes;
  if (primeiro === "auth") return partes.length === 2 && AUTH_PERMITIDOS.has(segundo);
  return PERMITIDOS.has(primeiro);
}

async function repassar(request: NextRequest, ctx: RouteContext<"/api/[...caminho]">) {
  const { caminho } = await ctx.params;
  if (!caminhoPermitido(caminho)) {
    return NextResponse.json({ detail: "não encontrado" }, { status: 404 });
  }
  if (!origemConfiavel(request)) {
    return NextResponse.json({ detail: "origem não permitida" }, { status: 403 });
  }
  const token = (await cookies()).get(COOKIE_SESSAO)?.value;
  if (!token) {
    return NextResponse.json({ detail: "sessão expirada" }, { status: 401 });
  }

  // Cada parte é recodificada: um "%2F" ou ".." dentro de um segmento não vira
  // separador de caminho na URL da API.
  const url = urlDaApi(caminho.map(encodeURIComponent).join("/"));
  url.search = request.nextUrl.search;

  const cabecalhos: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
    ...cabecalhosDoBff(request),
  };
  const tipo = request.headers.get("content-type");
  if (tipo) cabecalhos["Content-Type"] = tipo; // inclui o boundary do multipart (upload)

  const temCorpo = !["GET", "HEAD"].includes(request.method);
  const resposta = await fetch(url, {
    method: request.method,
    headers: cabecalhos,
    // O corpo vai em stream (o upload de PDF não é carregado inteiro na memória)
    body: temCorpo ? request.body : undefined,
    // @ts-expect-error: "duplex" é exigido pelo fetch do Node para corpo em stream
    duplex: temCorpo ? "half" : undefined,
    cache: "no-store",
    redirect: "manual",
  });

  // Só os cabeçalhos que o cliente precisa (nada de repassar tudo da API).
  const saida = new Headers();
  for (const nome of ["content-type", "retry-after"]) {
    const valor = resposta.headers.get(nome);
    if (valor) saida.set(nome, valor);
  }
  const resultado = new NextResponse(resposta.body, { status: resposta.status, headers: saida });
  // Token expirado ou revogado ("sair de todos"): o cookie não serve mais.
  if (resposta.status === 401) resultado.cookies.delete(COOKIE_SESSAO);
  return resultado;
}

export const GET = repassar;
export const POST = repassar;
export const PATCH = repassar;
export const DELETE = repassar;
