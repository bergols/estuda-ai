"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, dados, type Esquemas } from "./api";
import { sairPelaPonte } from "./desktop";
import { DESKTOP } from "./plataforma";

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
  flashcards: (id: number) => ["disciplinas", id, "flashcards"] as const,
  questoes: (id: number) => ["disciplinas", id, "questoes"] as const,
  fila: ["revisoes", "hoje"] as const,
  gastos: ["gastos"] as const,
  sessoes: ["sessoes"] as const,
  analytics: (rota: string, disciplinaId?: number) => ["analytics", rota, disciplinaId ?? "todas"] as const,
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

// ------------------------------------------------------------ geração com IA

const caminhoDisciplina = (id: number) => ({ params: { path: { disciplina_id: id } } });

export function usePerguntar(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (pergunta: string) =>
      dados(api.POST("/disciplinas/{disciplina_id}/perguntar", { ...caminhoDisciplina(disciplinaId), body: { pergunta } })),
    // Cada geração entra na auditoria: os gastos mudaram
    onSuccess: () => cliente.invalidateQueries({ queryKey: chaves.gastos }),
  });
}

export function useFlashcards(disciplinaId: number) {
  return useQuery({
    queryKey: chaves.flashcards(disciplinaId),
    queryFn: () => dados(api.GET("/disciplinas/{disciplina_id}/flashcards", caminhoDisciplina(disciplinaId))),
  });
}

export function useGerarFlashcards(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: Esquemas["GerarEntrada"]) =>
      dados(api.POST("/disciplinas/{disciplina_id}/flashcards/gerar", { ...caminhoDisciplina(disciplinaId), body: corpo })),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: chaves.flashcards(disciplinaId) });
      cliente.invalidateQueries({ queryKey: chaves.fila }); // card novo já entra na fila
      cliente.invalidateQueries({ queryKey: chaves.gastos });
    },
  });
}

export function useQuestoes(disciplinaId: number) {
  return useQuery({
    queryKey: chaves.questoes(disciplinaId),
    queryFn: () => dados(api.GET("/disciplinas/{disciplina_id}/questoes", caminhoDisciplina(disciplinaId))),
  });
}

export function useGerarQuestoes(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: Esquemas["GerarEntrada"]) =>
      dados(api.POST("/disciplinas/{disciplina_id}/questoes/gerar", { ...caminhoDisciplina(disciplinaId), body: corpo })),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: chaves.questoes(disciplinaId) });
      cliente.invalidateQueries({ queryKey: chaves.gastos });
    },
  });
}

export function useResponderQuestao(disciplinaId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ questaoId, ...corpo }: Esquemas["TentativaEntrada"] & { questaoId: number }) =>
      dados(
        api.POST("/disciplinas/{disciplina_id}/questoes/{questao_id}/tentativas", {
          params: { path: { disciplina_id: disciplinaId, questao_id: questaoId } },
          body: corpo,
        }),
      ),
    // A resposta alimenta o analytics (acerto por semana etc.)
    onSuccess: () => cliente.invalidateQueries({ queryKey: ["analytics"] }),
  });
}

// ---------------------------------------------------------- revisão (SM-2)

export function useFilaDoDia(disciplinaId?: number) {
  return useQuery({
    queryKey: [...chaves.fila, disciplinaId ?? "todas"],
    queryFn: () =>
      dados(api.GET("/revisoes/hoje", { params: { query: { limite: 100, disciplina_id: disciplinaId } } })),
    // A sessão trabalha sobre uma "foto" da fila: recarregar no meio (ao voltar para a
    // aba, por exemplo) tiraria da lista os cards já revisados e embaralharia a posição.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}

export function useRevisar() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ flashcardId, ...corpo }: Esquemas["RevisaoEntrada"] & { flashcardId: number }) =>
      dados(api.POST("/revisoes/{flashcard_id}", { params: { path: { flashcard_id: flashcardId } }, body: corpo })),
    // A fila NÃO é invalidada aqui (ver useFilaDoDia); o analytics sim.
    onSuccess: () => cliente.invalidateQueries({ queryKey: ["analytics"] }),
  });
}

// ------------------------------------------------------- sessões de estudo

/** Histórico de sessões (as do app desktop, depois de sincronizadas). */
export function useSessoes() {
  return useQuery({
    queryKey: chaves.sessoes,
    queryFn: () => dados(api.GET("/sessoes", { params: { query: { limite: 30 } } })),
  });
}

// ------------------------------------------------------------- analytics

/** Opções comuns: ao trocar o filtro, a tela mantém o desenho anterior (esmaecido)
 * até os dados novos chegarem, em vez de piscar um "carregando". */
const filtro = (disciplinaId?: number) => ({ params: { query: { disciplina_id: disciplinaId } } });
const comum = { placeholderData: keepPreviousData };

export function useSequencia(d?: number) {
  return useQuery({ queryKey: chaves.analytics("sequencia", d), queryFn: () => dados(api.GET("/analytics/sequencia", filtro(d))), ...comum });
}
export function useEvolucaoDiaria(d?: number) {
  return useQuery({ queryKey: chaves.analytics("evolucao-diaria", d), queryFn: () => dados(api.GET("/analytics/evolucao/diaria", filtro(d))), ...comum });
}
export function useEvolucaoSemanal(d?: number) {
  return useQuery({ queryKey: chaves.analytics("evolucao-semanal", d), queryFn: () => dados(api.GET("/analytics/evolucao/semanal", filtro(d))), ...comum });
}
export function useCalendario(d?: number) {
  return useQuery({ queryKey: chaves.analytics("calendario", d), queryFn: () => dados(api.GET("/analytics/calendario", filtro(d))), ...comum });
}
export function usePrevisao(d?: number) {
  return useQuery({ queryKey: chaves.analytics("previsao", d), queryFn: () => dados(api.GET("/analytics/previsao", filtro(d))), ...comum });
}
export function useCardsDificeis(d?: number) {
  return useQuery({
    queryKey: chaves.analytics("cards-dificeis", d),
    queryFn: () => dados(api.GET("/analytics/cards-dificeis", { params: { query: { disciplina_id: d, limite: 10 } } })),
    ...comum,
  });
}
export function useCustos(d?: number) {
  return useQuery({ queryKey: chaves.analytics("custos", d), queryFn: () => dados(api.GET("/analytics/custos", filtro(d))), ...comum });
}

/** REFRESH da materialized view (pela função SECURITY DEFINER na API). */
export function useAtualizarAnalytics() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: () => dados(api.POST("/analytics/atualizar")),
    onSuccess: () => cliente.invalidateQueries({ queryKey: ["analytics"] }),
  });
}

// --------------------------------------------------------------------- conta

export function useGastos() {
  return useQuery({ queryKey: chaves.gastos, queryFn: () => dados(api.GET("/gastos")) });
}

/** Sair deste aparelho: o BFF apaga o cookie (no desktop, o Rust apaga o token do cofre). */
async function apagarCookie() {
  if (DESKTOP) return sairPelaPonte();
  await fetch("/api/sessao", { method: "DELETE" });
}

export function useSair() {
  return useMutation({ mutationFn: apagarCookie });
}

/** Sair de TODOS os aparelhos: a API incrementa versao_token (todo JWT emitido antes
 * deixa de valer) e depois o cookie deste aparelho também é apagado. */
export function useSairDeTodos() {
  return useMutation({
    mutationFn: async () => {
      await dados(api.POST("/auth/sair-de-todos"));
      await apagarCookie();
    },
  });
}
