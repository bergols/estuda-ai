import { NextResponse } from "next/server";

import { clienteNativo } from "@/lib/bff";
import { erroDaApi, loginNaApi } from "@/lib/sessao";

/**
 * Login para o app desktop: devolve o token no corpo, e o app o guarda no cofre do
 * sistema (Keychain/Gerenciador de Credenciais). A web nunca usa esta rota: ela usa
 * /api/sessao, que grava o cookie httpOnly. Pedido vindo de navegador é recusado
 * (ver clienteNativo em lib/bff.ts).
 */
export async function POST(request: Request) {
  if (!clienteNativo(request.headers.get("sec-fetch-site"))) {
    return NextResponse.json({ detail: "rota exclusiva do app desktop" }, { status: 403 });
  }
  const resposta = await loginNaApi(request);
  if (!resposta.ok) return erroDaApi(resposta);
  const { access_token, expira_em } = await resposta.json();
  return NextResponse.json({ access_token, expira_em });
}
