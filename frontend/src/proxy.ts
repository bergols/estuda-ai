import { NextResponse, type NextRequest } from "next/server";

/**
 * Checagem OTIMISTA de sessão antes de renderizar uma página: sem o cookie, vai para
 * /login. Ela só olha se o cookie existe (não valida o token); quem garante o acesso
 * é a API, que confere o JWT em toda chamada. Um cookie inválido passa daqui, mas a
 * primeira chamada à API dá 401 e o cliente volta para /login.
 */
const COOKIE_SESSAO = "estuda_ai_sessao";

export function proxy(request: NextRequest) {
  const logado = request.cookies.has(COOKIE_SESSAO);
  const { pathname } = request.nextUrl;
  if (pathname === "/login") {
    return logado ? NextResponse.redirect(new URL("/", request.url)) : NextResponse.next();
  }
  if (!logado) return NextResponse.redirect(new URL("/login", request.url));
  return NextResponse.next();
}

export const config = {
  // Páginas apenas: /api (o BFF responde 401 sozinho), arquivos do Next e estáticos ficam de fora
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
