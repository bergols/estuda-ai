import { describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

import { caminhoDaPonte, fetchPelaPonte, respostaDaPonte } from "./desktop";

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

describe("fetchPelaPonte", () => {
  it("manda o tipo do corpo em x-tipo, não em content-type (que o IPC sobrescreve)", async () => {
    invoke.mockResolvedValue({ status: 200, tipo: "application/json", retry_after: null, corpo: "{}" });
    await fetchPelaPonte("http://tauri.localhost/api/spotify/preferencias", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ no_intervalo: "pausar" }),
    });
    const [comando, corpo, opcoes] = invoke.mock.calls.at(-1)!;
    expect(comando).toBe("chamar_api");
    expect(opcoes.headers).toEqual({ "x-metodo": "PUT", "x-caminho": "spotify/preferencias", "x-tipo": "application/json" });
    expect(opcoes.headers).not.toHaveProperty("content-type");
    expect(new TextDecoder().decode(corpo)).toBe('{"no_intervalo":"pausar"}');
  });

  it("upload multipart leva o boundary no x-tipo", async () => {
    invoke.mockResolvedValue({ status: 201, tipo: "application/json", retry_after: null, corpo: "{}" });
    const form = new FormData();
    form.append("arquivo", new Blob(["%PDF"]), "a.pdf");
    await fetchPelaPonte("http://tauri.localhost/api/disciplinas/1/materiais", { method: "POST", body: form });
    const opcoes = invoke.mock.calls.at(-1)![2];
    expect(opcoes.headers["x-tipo"]).toMatch(/^multipart\/form-data; boundary=/);
  });
});
