import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import {
  COOKIE_SESSAO,
  cabecalhosDoBff,
  opcoesDoCookie,
  origemConfiavel,
  urlDaApi,
} from "@/lib/sessao";

/** Login: troca e-mail e senha por um cookie httpOnly com o JWT. */
export async function POST(request: Request) {
  if (!origemConfiavel(request)) {
    return NextResponse.json({ detail: "origem não permitida" }, { status: 403 });
  }
  const { email, senha } = (await request.json().catch(() => ({}))) as {
    email?: unknown;
    senha?: unknown;
  };
  if (typeof email !== "string" || typeof senha !== "string") {
    return NextResponse.json({ detail: "informe e-mail e senha" }, { status: 422 });
  }

  // A API espera o formulário do fluxo "password" do OAuth2 (username/password).
  const resposta = await fetch(urlDaApi("auth/login"), {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      ...cabecalhosDoBff(request),
    },
    body: new URLSearchParams({ username: email, password: senha }),
    cache: "no-store",
  });
  const corpo = await resposta.json().catch(() => ({ detail: "erro na API" }));
  if (!resposta.ok) {
    const cabecalhos: Record<string, string> = {};
    const retry = resposta.headers.get("retry-after");
    if (retry) cabecalhos["Retry-After"] = retry;
    return NextResponse.json({ detail: corpo.detail }, { status: resposta.status, headers: cabecalhos });
  }

  (await cookies()).set(
    COOKIE_SESSAO,
    corpo.access_token,
    opcoesDoCookie(new Date(corpo.expira_em)),
  );
  // O token NÃO volta no corpo: o JavaScript da página não precisa (nem deve) vê-lo.
  return NextResponse.json({ expira_em: corpo.expira_em });
}

/** Sair deste aparelho: apaga o cookie (o token continua válido até expirar; para
 * derrubar todos, use "sair de todos", que revoga na API). */
export async function DELETE(request: Request) {
  if (!origemConfiavel(request)) {
    return NextResponse.json({ detail: "origem não permitida" }, { status: 403 });
  }
  (await cookies()).delete(COOKIE_SESSAO);
  return new NextResponse(null, { status: 204 });
}
