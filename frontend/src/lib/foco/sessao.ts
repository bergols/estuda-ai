import type { Esquemas } from "@/lib/api";

import {
  type Config,
  type EstadoTimer,
  iniciar,
  momento,
  pausar,
  pausasPlanejadasConcluidas,
  plano,
  retomar,
  statusAoEncerrar,
} from "./timer";

/**
 * A sessão em andamento, como a janela a mantém: `dados` é exatamente o corpo que a
 * API recebe em /sessoes/sincronizar (com as pausas e os eventos acumulados), e
 * `local` é o que só o app precisa para continuar de onde parou. Os dois vão para o
 * SQLite a cada mudança (lib/foco/local.ts).
 *
 * Funções puras, com `agora` e o gerador de chaves como parâmetros (testes em
 * sessao.test.ts). A chave de idempotência de cada pausa e evento é criada UMA vez,
 * quando o fato acontece, e gravada junto: reenviar a sessão reenvia as mesmas chaves.
 */

export type SessaoEnvio = Esquemas["SessaoEnvio"];

export type EstadoLocal = {
  timer: EstadoTimer;
  /** Índices (no plano) das pausas planejadas já registradas em dados.pausas */
  pausasRegistradas: number[];
  /** A janela perdeu o foco durante uma fase de FOCO, neste instante */
  foraDesdeMs: number | null;
};

export type SessaoAtiva = { dados: SessaoEnvio; local: EstadoLocal };

export type GeradorDeChave = () => string;

/** Menos que isso fora da janela (um alt-tab acidental) não vira interrupção. */
export const SAIDA_MINIMA_MS = 3_000;

const iso = (ms: number) => new Date(ms).toISOString();

export function configDe(d: SessaoEnvio): Config {
  return {
    metodo: d.metodo,
    focoMin: d.foco_min,
    pausaMin: d.pausa_min,
    ciclos: d.ciclos,
    pausaLongaMin: d.pausa_longa_min ?? null,
    ciclosAtePausaLonga: d.ciclos_ate_pausa_longa ?? null,
  };
}

export function novaSessao(args: {
  config: Config;
  disciplinaId: number | null;
  meta: string | null;
  sistema: SessaoEnvio["sistema"];
  agora: number;
  novaChave: GeradorDeChave;
}): SessaoAtiva {
  const { config: c } = args;
  return {
    dados: {
      chave: args.novaChave(),
      metodo: c.metodo,
      foco_min: c.focoMin,
      pausa_min: c.pausaMin,
      ciclos: c.ciclos,
      pausa_longa_min: c.pausaLongaMin,
      ciclos_ate_pausa_longa: c.ciclosAtePausaLonga,
      meta: args.meta?.trim() || null,
      disciplina_id: args.disciplinaId,
      sistema: args.sistema,
      status: "em_andamento",
      iniciada_em: iso(args.agora),
      terminada_em: null,
      pausas: [],
      eventos: [],
    },
    local: { timer: iniciar(args.agora), pausasRegistradas: [], foraDesdeMs: null },
  };
}

/**
 * Registra as pausas planejadas que terminaram desde a última chamada (o "tick" da tela
 * chama isto). Devolve a mesma referência quando nada mudou, para a tela não gravar à toa.
 */
export function registrarPausasPlanejadas(s: SessaoAtiva, agora: number, novaChave: GeradorDeChave): SessaoAtiva {
  const fases = plano(configDe(s.dados));
  const novas = pausasPlanejadasConcluidas(fases, s.local.timer, agora).filter(
    (p) => !s.local.pausasRegistradas.includes(p.indice),
  );
  if (novas.length === 0) return s;
  return {
    dados: {
      ...s.dados,
      pausas: [
        ...(s.dados.pausas ?? []),
        ...novas.map((p) => ({ chave: novaChave(), tipo: p.tipo, iniciada_em: iso(p.inicioMs), terminada_em: iso(p.fimMs) })),
      ],
    },
    local: { ...s.local, pausasRegistradas: [...s.local.pausasRegistradas, ...novas.map((p) => p.indice)] },
  };
}

export function emFoco(s: SessaoAtiva, agora: number): boolean {
  const m = momento(plano(configDe(s.dados)), s.local.timer, agora);
  return m.fase?.tipo === "foco" && !m.pausado;
}

export function pausarSessao(s: SessaoAtiva, agora: number): SessaoAtiva {
  if (!emFoco(s, agora)) return s; // ver o comentário de pausar() em timer.ts
  return { ...s, local: { ...s.local, timer: pausar(s.local.timer, agora) } };
}

export function retomarSessao(s: SessaoAtiva, agora: number, novaChave: GeradorDeChave): SessaoAtiva {
  const { estado, pausa } = retomar(s.local.timer, agora);
  if (!pausa) return s;
  return {
    dados: {
      ...s.dados,
      pausas: [
        ...(s.dados.pausas ?? []),
        { chave: novaChave(), tipo: "manual", iniciada_em: iso(pausa.inicioMs), terminada_em: iso(pausa.fimMs) },
      ],
    },
    local: { ...s.local, timer: estado },
  };
}

/** A janela perdeu o foco. Só conta durante o foco (nas pausas, sair é o esperado). */
export function saiuDaJanela(s: SessaoAtiva, agora: number): SessaoAtiva {
  if (s.local.foraDesdeMs !== null || !emFoco(s, agora)) return s;
  return { ...s, local: { ...s.local, foraDesdeMs: agora } };
}

export function voltouParaJanela(s: SessaoAtiva, agora: number, novaChave: GeradorDeChave): SessaoAtiva {
  const desde = s.local.foraDesdeMs;
  if (desde === null) return s;
  const local = { ...s.local, foraDesdeMs: null };
  if (agora - desde < SAIDA_MINIMA_MS) return { ...s, local };
  return {
    dados: {
      ...s.dados,
      eventos: [
        ...(s.dados.eventos ?? []),
        { chave: novaChave(), tipo: "saida_janela", ocorrido_em: iso(desde), duracao_s: Math.round((agora - desde) / 1000) },
      ],
    },
    local,
  };
}

/** Programa ou site bloqueado (sessão 4) e outros eventos instantâneos. */
export function registrarEvento(
  s: SessaoAtiva,
  tipo: "programa_bloqueado" | "site_bloqueado" | "saida_emergencia",
  agora: number,
  novaChave: GeradorDeChave,
  detalhe: string | null = null,
): SessaoAtiva {
  return {
    ...s,
    dados: {
      ...s.dados,
      eventos: [...(s.dados.eventos ?? []), { chave: novaChave(), tipo, ocorrido_em: iso(agora), detalhe }],
    },
  };
}

/**
 * Encerra: fecha a pausa manual e a saída da janela que estiverem abertas, registra a
 * saída de emergência (se for o caso) e decide concluída × abandonada pelo foco cumprido.
 */
export function encerrar(
  s: SessaoAtiva,
  agora: number,
  novaChave: GeradorDeChave,
  opcoes: { emergencia?: boolean } = {},
): SessaoAtiva {
  let atual = registrarPausasPlanejadas(s, agora, novaChave);
  atual = retomarSessao(atual, agora, novaChave);
  atual = voltouParaJanela(atual, agora, novaChave);
  if (opcoes.emergencia) atual = registrarEvento(atual, "saida_emergencia", agora, novaChave);
  const c = configDe(atual.dados);
  const status = statusAoEncerrar(plano(c), c, atual.local.timer, agora);
  return { ...atual, dados: { ...atual.dados, status, terminada_em: iso(agora) } };
}
