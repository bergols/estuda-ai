import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { caminhoDaApi, caminhoPermitido, tokenBearer } from "@/lib/bff";
import { COOKIE_SESSAO, cabecalhosDoBff, chamarApi, origemConfiavel } from "@/lib/sessao";

/**
 * Repassa /api/<caminho> para a API, com o JWT em "Authorization": o do cookie (web)
 * ou o que o app desktop mandou como Bearer. Regras (allowlist, CSRF, IP) em
 * lib/bff.ts, com testes em lib/bff.test.ts.
 */

// Gerações com IA levam dezenas de segundos. Na Vercel (plano Hobby com Fluid compute)
// o padrão já é 300 s; o valor explícito documenta a expectativa e vale em outros hosts.
export const maxDuration = 120;

async function repassar(request: NextRequest, ctx: RouteContext<"/api/[...caminho]">) {
  const { caminho } = await ctx.params;
  if (!caminhoPermitido(caminho)) {
    return NextResponse.json({ detail: "não encontrado" }, { status: 404 });
  }
  // App desktop: o token vem no cabeçalho, e a checagem de origem não se aplica (ver
  // tokenBearer em lib/bff.ts). Web: o token vem do cookie, e a checagem é obrigatória.
  const bearer = tokenBearer(request.headers.get("authorization"));
  if (!bearer && !origemConfiavel(request)) {
    return NextResponse.json({ detail: "origem não permitida" }, { status: 403 });
  }
  const token = bearer ?? (await cookies()).get(COOKIE_SESSAO)?.value;
  if (!token) {
    return NextResponse.json({ detail: "sessão expirada" }, { status: 401 });
  }

  const caminhoComBusca = caminhoDaApi(caminho) + request.nextUrl.search;

  const cabecalhos: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
    ...cabecalhosDoBff(request),
  };
  const tipo = request.headers.get("content-type");
  if (tipo) cabecalhos["Content-Type"] = tipo; // inclui o boundary do multipart (upload)

  const temCorpo = !["GET", "HEAD"].includes(request.method);
  const resposta = await chamarApi(caminhoComBusca, {
    method: request.method,
    headers: cabecalhos,
    // O corpo vai em stream (o upload de PDF não é carregado inteiro na memória)
    body: temCorpo ? request.body : undefined,
    // @ts-expect-error: "duplex" é exigido pelo fetch do Node para corpo em stream
    duplex: temCorpo ? "half" : undefined,
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
  if (resposta.status === 401 && !bearer) resultado.cookies.delete(COOKIE_SESSAO);
  return resultado;
}

export const GET = repassar;
export const POST = repassar;
export const PUT = repassar;
export const PATCH = repassar;
export const DELETE = repassar;
