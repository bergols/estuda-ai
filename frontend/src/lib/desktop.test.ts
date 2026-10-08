import { describe, expect, it } from "vitest";

import { caminhoDaPonte, respostaDaPonte } from "./desktop";

describe("ponte do desktop", () => {
  it("tira o /api/ e mantém a busca codificada", () => {
    const url = new URL("http://tauri.localhost/api/disciplinas/5/busca?q=%C3%ADndice&modo=hibrida");
    expect(caminhoDaPonte(url)).toBe("disciplinas/5/busca?q=%C3%ADndice&modo=hibrida");
  });

  it("vira um Response com status, tipo e Retry-After", async () => {
    const r = respostaDaPonte({
      status: 429,
      tipo: "application/json",
      retry_after: "30",
      corpo: '{"detail":"muitas tentativas"}',
    });
    expect(r.status).toBe(429);
    expect(r.headers.get("retry-after")).toBe("30");
    expect(await r.json()).toEqual({ detail: "muitas tentativas" });
  });

  it("204 sai sem corpo (o construtor do Response recusaria um corpo)", () => {
    const r = respostaDaPonte({ status: 204, tipo: null, retry_after: null, corpo: "" });
    expect(r.status).toBe(204);
    expect(r.body).toBeNull();
  });
});
