import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { COOKIE_SESSAO, erroDaApi, loginNaApi, opcoesDoCookie, origemConfiavel } from "@/lib/sessao";

/** Login: troca e-mail e senha por um cookie httpOnly com o JWT. */
export async function POST(request: Request) {
  if (!origemConfiavel(request)) {
    return NextResponse.json({ detail: "origem não permitida" }, { status: 403 });
  }
  const resposta = await loginNaApi(request);
  if (!resposta.ok) return erroDaApi(resposta);
  const corpo = await resposta.json();

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
