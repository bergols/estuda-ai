import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Todo método HTTP que a API usa (no contrato openapi.json) precisa de um handler
 * exportado no repasse do BFF. Na fase 7, PUT /spotify/preferencias funcionava no app
 * desktop (o Rust aceita PUT) e na API, mas o BFF da Vercel não exportava PUT: a web
 * respondia 405 e as playlists nunca eram salvas.
 */
describe("BFF repassa todos os métodos do contrato", () => {
  const raiz = join(__dirname, "..", "..");
  const contrato = JSON.parse(readFileSync(join(raiz, "openapi.json"), "utf8")) as {
    paths: Record<string, Record<string, unknown>>;
  };
  const metodos = new Set(
    Object.values(contrato.paths).flatMap((ops) => Object.keys(ops).map((m) => m.toUpperCase())),
  );
  const rota = readFileSync(join(raiz, "src", "app", "api", "[...caminho]", "route.web.ts"), "utf8");

  it.each([...metodos])("%s tem handler", (metodo) => {
    expect(rota).toMatch(new RegExp(`^export const ${metodo} = repassar;$`, "m"));
  });
});
