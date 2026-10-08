"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, dados, type Esquemas } from "./api";

/**
 * Chaves do cache do TanStack Query. Hierárquicas: invalidar ["disciplinas", id]
 * invalida também ["disciplinas", id, "materiais"], etc.
 */
export const chaves = {
  eu: ["eu"] as const,
  disciplinas: ["disciplinas"] as const,
  disciplina: (id: number) => ["disciplinas", id] as const,
  materiais: (id: number) => ["disciplinas", id, "materiais"] as const,
  busca: (id: number, q: string, modo: Modo) => ["disciplinas", id, "busca", modo, q] as const,
};

export type Modo = "semantica" | "textual" | "hibrida";
type Material = Esquemas["MaterialLer"];

const EM_ANDAMENTO: Material["status"][] = ["pendente", "processando"];

export function useEu() {
  return useQuery({
    queryKey: chaves.eu,
    queryFn: () => dados(api.GET("/auth/eu")),
    staleTime: 5 * 60_000,
  });
}

export function useDisciplinas() {
  return useQuery({
    queryKey: chaves.disciplinas,
    queryFn: () => dados(api.GET("/disciplinas")),
  });
}

export function useDisciplina(id: number) {
  return useQuery({
    queryKey: chaves.disciplina(id),
    queryFn: () => dados(api.GET("/disciplinas/{disciplina_id}", { params: { path: { disciplina_id: id } } })),
  });
}

export function useCriarDisciplina() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: Esquemas["DisciplinaCriar"]) => dados(api.POST("/disciplinas", { body: corpo })),
    onSuccess: () => cliente.invalidateQueries({ queryKey: chaves.disciplinas }),
  });
}

export function useMateriais(disciplinaId: number) {
  return useQuery({
    queryKey: chaves.materiais(disciplinaId),
    queryFn: () =>
      dados(api.GET("/disciplinas/{disciplina_id}/materiais", { params: { path: { disciplina_id: disciplinaId } } })),
    // Polling SÓ enquanto algum material está sendo processado (o upload responde 202
    // e o processamento roda em background na API). Depois disso, para de perguntar.
    refetchInterval: (consulta) =>
      consulta.state.data?.some((m) => EM_ANDAMENTO.includes(m.status)) ? 2000 : false,
  });
}

export function useEnviarMaterial(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ arquivo, titulo }: { arquivo: File; titulo?: string }) => {
      const form = new FormData();
      form.append("arquivo", arquivo);
      if (titulo) form.append("titulo", titulo);
      return dados(
        api.POST("/disciplinas/{disciplina_id}/materiais", {
          params: { path: { disciplina_id: disciplinaId } },
          // multipart: o navegador monta o corpo e o boundary; o tipo gerado espera
          // campos de texto, por isso o cast.
          body: form as unknown as { arquivo: string; titulo?: string | null },
          bodySerializer: (corpo) => corpo as unknown as FormData,
        }),
      );
    },
    onSuccess: () => cliente.invalidateQueries({ queryKey: chaves.materiais(disciplinaId) }),
  });
}

export function useReprocessar(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (materialId: number) =>
      dados(
        api.POST("/disciplinas/{disciplina_id}/materiais/{material_id}/reprocessar", {
          params: { path: { disciplina_id: disciplinaId, material_id: materialId } },
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: chaves.materiais(disciplinaId) }),
  });
}

export function useApagarMaterial(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (materialId: number) =>
      dados(
        api.DELETE("/disciplinas/{disciplina_id}/materiais/{material_id}", {
          params: { path: { disciplina_id: disciplinaId, material_id: materialId } },
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: chaves.materiais(disciplinaId) }),
  });
}

export function useBusca(disciplinaId: number, q: string, modo: Modo) {
  return useQuery({
    queryKey: chaves.busca(disciplinaId, q, modo),
    queryFn: () =>
      dados(
        api.GET("/disciplinas/{disciplina_id}/busca", {
          params: { path: { disciplina_id: disciplinaId }, query: { q, modo, k: 10 } },
        }),
      ),
    enabled: q.trim().length > 0,
    staleTime: 5 * 60_000,
  });
}
