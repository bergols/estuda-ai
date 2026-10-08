import { describe, expect, it } from "vitest";

import { caminhoDaApi, caminhoPermitido, clienteNativo, ipDoCliente, origemConfiavel, tokenBearer } from "./bff";

describe("allowlist do repasse /api/<caminho>", () => {
  it.each([
    [["disciplinas"]],
    [["disciplinas", "5", "materiais"]],
    [["revisoes", "hoje"]],
    [["analytics", "evolucao", "diaria"]],
    [["gastos"]],
    [["sessoes", "sincronizar"]],
    [["spotify", "token"]],
    [["auth", "eu"]],
    [["auth", "sair-de-todos"]],
  ])("repassa %j", (partes) => {
    expect(caminhoPermitido(partes)).toBe(true);
  });

  it.each([
    [[]],
    [["auth", "login"]], // login só pela /api/sessao, que grava o cookie
    [["auth"]],
    [["auth", "eu", "extra"]],
    [["usuarios"]],
    [["health"]],
    [["docs"]],
    [["openapi.json"]],
    [["..", "health"]],
    // ".." no MEIO: o primeiro segmento é permitido, mas a URL subiria para /docs
    [["disciplinas", "..", "docs"]],
    [["disciplinas", "1", "..", "..", "auth", "login"]],
    [["disciplinas", ".", "x"]],
    [["disciplinas", "", "x"]],
  ])("recusa %j", (partes) => {
    expect(caminhoPermitido(partes)).toBe(false);
  });
});

describe("caminho montado para a API", () => {
  it("recodifica cada segmento: %2F não vira barra", () => {
    expect(caminhoDaApi(["disciplinas", "1/../../health"])).toBe("disciplinas/1%2F..%2F..%2Fhealth");
  });

  it('por que ".." é recusado em vez de codificado: %2E%2E também sobe de diretório', () => {
    // Documenta o comportamento do padrão WHATWG que motivou a regra
    expect(new URL("disciplinas/%2E%2E/docs", "http://api.local/").pathname).toBe("/docs");
  });
});

describe("origem (CSRF)", () => {
  it("leitura passa de qualquer origem", () => {
    expect(origemConfiavel("GET", "cross-site")).toBe(true);
  });

  it.each(["POST", "PATCH", "DELETE"])("%s do mesmo site passa", (metodo) => {
    expect(origemConfiavel(metodo, "same-origin")).toBe(true);
  });

  it.each(["cross-site", "same-site", "none"])("mutação com Sec-Fetch-Site=%s é recusada", (site) => {
    expect(origemConfiavel("POST", site)).toBe(false);
  });

  it("sem o header (navegador antigo, curl) passa: o cookie SameSite=Lax ainda protege", () => {
    expect(origemConfiavel("POST", null)).toBe(true);
  });
});

describe("IP do cliente", () => {
  it("pega o primeiro da lista do X-Forwarded-For", () => {
    expect(ipDoCliente("203.0.113.7, 10.0.0.1")).toBe("203.0.113.7");
  });

  it("sem header não inventa IP", () => {
    expect(ipDoCliente(null)).toBeNull();
    expect(ipDoCliente("")).toBeNull();
  });
});

describe("Bearer do app desktop", () => {
  it("extrai o token de 'Bearer <token>' (esquema sem diferenciar maiúsculas)", () => {
    expect(tokenBearer("Bearer abc.def.ghi")).toBe("abc.def.ghi");
    expect(tokenBearer("bearer abc")).toBe("abc");
    expect(tokenBearer("  Bearer   abc  ")).toBe("abc");
  });

  it.each([null, "", "Bearer", "Bearer ", "Basic dXNlcjpzZW5oYQ==", "Bearer a b", "Token abc"])(
    "%j não é um Bearer válido",
    (valor) => {
      expect(tokenBearer(valor)).toBeNull();
    },
  );
});

describe("login que devolve token (só cliente nativo)", () => {
  it("sem Sec-Fetch-Site (app desktop, curl) é cliente nativo", () => {
    expect(clienteNativo(null)).toBe(true);
  });

  it.each(["same-origin", "same-site", "cross-site", "none"])("navegador (Sec-Fetch-Site=%s) é recusado", (site) => {
    expect(clienteNativo(site)).toBe(false);
  });
});
