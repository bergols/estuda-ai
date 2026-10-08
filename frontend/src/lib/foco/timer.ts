/**
 * Motor do timer das sessões de estudo. Funções PURAS: recebem o instante atual
 * (`agora`, em ms) em vez de ler o relógio, e por isso são testadas com relógio falso
 * (timer.test.ts), sem esperar 25 minutos de verdade.
 *
 * O timer não conta "ticks" de setInterval. Ele guarda só o instante de início e as
 * pausas manuais, e CALCULA a fase atual por subtração. Um contador de ticks erra
 * quando o navegador desacelera a aba em segundo plano, quando o notebook dorme ou
 * quando o app fecha e reabre; a subtração de instantes não erra.
 *
 *   tempo de plano = (agora − início) − pausas manuais
 *
 * O "plano" é a sequência de fases (foco, pausa curta, foco, ..., pausa longa, ...).
 * As pausas planejadas fazem parte do plano; a pausa MANUAL (botão "pausar") congela
 * o plano.
 */

export type Metodo = "pomodoro" | "bloco" | "52_17" | "personalizado";

export type Config = {
  metodo: Metodo;
  focoMin: number;
  pausaMin: number;
  ciclos: number;
  pausaLongaMin: number | null;
  ciclosAtePausaLonga: number | null;
};

export type TipoFase = "foco" | "pausa_curta" | "pausa_longa";

export type Fase = { tipo: TipoFase; ciclo: number; duracaoMs: number; inicioNoPlanoMs: number };

/** Estado interno do timer (vai para o SQLite junto com a sessão, para retomar). */
export type EstadoTimer = {
  inicioMs: number;
  pausasManuais: { inicioMs: number; fimMs: number }[];
  pausadoDesdeMs: number | null;
};

export const MINUTO = 60_000;

export const PADROES: Record<Metodo, Config> = {
  pomodoro: { metodo: "pomodoro", focoMin: 25, pausaMin: 5, ciclos: 4, pausaLongaMin: 15, ciclosAtePausaLonga: 4 },
  bloco: { metodo: "bloco", focoMin: 60, pausaMin: 0, ciclos: 1, pausaLongaMin: null, ciclosAtePausaLonga: null },
  "52_17": { metodo: "52_17", focoMin: 52, pausaMin: 17, ciclos: 2, pausaLongaMin: null, ciclosAtePausaLonga: null },
  personalizado: { metodo: "personalizado", focoMin: 40, pausaMin: 10, ciclos: 3, pausaLongaMin: null, ciclosAtePausaLonga: null },
};

/**
 * As mesmas regras dos CHECKs de sessoes_estudo (migration "sessoes_de_estudo"). O
 * banco é a garantia final; conferir aqui evita criar uma sessão que o servidor vai
 * recusar depois, horas mais tarde, quando o app sincronizar.
 */
export function erroDaConfig(c: Config): string | null {
  const inteiro = (v: number, min: number, max: number) => Number.isInteger(v) && v >= min && v <= max;
  if (!inteiro(c.focoMin, 1, 240)) return "foco entre 1 e 240 minutos";
  if (!inteiro(c.pausaMin, 0, 60)) return "pausa entre 0 e 60 minutos";
  if (!inteiro(c.ciclos, 1, 12)) return "entre 1 e 12 ciclos";
  if ((c.pausaLongaMin === null) !== (c.ciclosAtePausaLonga === null)) return "pausa longa incompleta";
  if (c.pausaLongaMin !== null && !inteiro(c.pausaLongaMin, 1, 90)) return "pausa longa entre 1 e 90 minutos";
  if (c.ciclosAtePausaLonga !== null && !inteiro(c.ciclosAtePausaLonga, 2, 12))
    return "pausa longa a cada 2 a 12 ciclos";
  if (c.metodo === "52_17" && (c.focoMin !== 52 || c.pausaMin !== 17)) return "52/17 é 52 de foco e 17 de pausa";
  if (c.metodo === "bloco" && (c.pausaMin !== 0 || c.ciclos !== 1 || c.pausaLongaMin !== null))
    return "bloco contínuo não tem pausas";
  return null;
}

/** Sequência de fases. Depois do último foco não há pausa: a sessão acaba. */
export function plano(c: Config): Fase[] {
  const fases: Fase[] = [];
  let t = 0;
  const empurrar = (tipo: TipoFase, ciclo: number, minutos: number) => {
    fases.push({ tipo, ciclo, duracaoMs: minutos * MINUTO, inicioNoPlanoMs: t });
    t += minutos * MINUTO;
  };
  for (let ciclo = 1; ciclo <= c.ciclos; ciclo++) {
    empurrar("foco", ciclo, c.focoMin);
    if (ciclo === c.ciclos) break;
    const longa = c.pausaLongaMin !== null && c.ciclosAtePausaLonga !== null && ciclo % c.ciclosAtePausaLonga === 0;
    if (longa) empurrar("pausa_longa", ciclo, c.pausaLongaMin!);
    else if (c.pausaMin > 0) empurrar("pausa_curta", ciclo, c.pausaMin);
  }
  return fases;
}

export function duracaoDoPlano(fases: Fase[]): number {
  const ultima = fases.at(-1);
  return ultima ? ultima.inicioNoPlanoMs + ultima.duracaoMs : 0;
}

export function focoPlanejadoMs(c: Config): number {
  return c.focoMin * MINUTO * c.ciclos;
}

export function iniciar(agora: number): EstadoTimer {
  return { inicioMs: agora, pausasManuais: [], pausadoDesdeMs: null };
}

/**
 * Pausa manual ("pausar"): congela o plano. Só durante o FOCO (a tela desabilita o
 * botão nas pausas planejadas): pausar no meio de uma pausa planejada faria os dois
 * intervalos se sobreporem, e o tempo de pausa seria contado em dobro.
 */
export function pausar(e: EstadoTimer, agora: number): EstadoTimer {
  return e.pausadoDesdeMs === null ? { ...e, pausadoDesdeMs: agora } : e;
}

/** Retoma; devolve também o intervalo da pausa, que vira uma pausa "manual" no servidor. */
export function retomar(e: EstadoTimer, agora: number): { estado: EstadoTimer; pausa: { inicioMs: number; fimMs: number } | null } {
  if (e.pausadoDesdeMs === null) return { estado: e, pausa: null };
  const pausa = { inicioMs: e.pausadoDesdeMs, fimMs: Math.max(agora, e.pausadoDesdeMs) };
  return { estado: { ...e, pausadoDesdeMs: null, pausasManuais: [...e.pausasManuais, pausa] }, pausa };
}

/** Tempo de plano decorrido: o relógio menos as pausas manuais (inclusive a atual). */
export function tempoDePlano(e: EstadoTimer, agora: number): number {
  const fim = e.pausadoDesdeMs ?? agora;
  const pausado = e.pausasManuais.reduce((soma, p) => soma + (p.fimMs - p.inicioMs), 0);
  return Math.max(0, fim - e.inicioMs - pausado);
}

/**
 * Converte um ponto do plano no instante do relógio (somando as pausas manuais antes
 * dele). Uma pausa manual que começa EXATAMENTE nesse ponto conta como "antes" para o
 * início de uma fase e como "depois" para o fim (`fim = true`): a fase não engole a pausa.
 */
export function noRelogio(e: EstadoTimer, tPlano: number, fim = false): number {
  let instante = e.inicioMs + tPlano;
  let pausadoAntes = 0;
  for (const p of [...e.pausasManuais].sort((a, b) => a.inicioMs - b.inicioMs)) {
    const posicaoNoPlano = p.inicioMs - e.inicioMs - pausadoAntes;
    if (fim ? posicaoNoPlano >= tPlano : posicaoNoPlano > tPlano) break;
    instante += p.fimMs - p.inicioMs;
    pausadoAntes += p.fimMs - p.inicioMs;
  }
  return instante;
}

export type Momento = {
  fase: Fase | null; // null: o plano acabou
  indice: number;
  restanteMs: number; // da fase atual
  focoCumpridoMs: number; // foco do plano já decorrido (sem pausas)
  terminado: boolean;
  pausado: boolean;
};

export function momento(fases: Fase[], e: EstadoTimer, agora: number): Momento {
  const t = tempoDePlano(e, agora);
  const pausado = e.pausadoDesdeMs !== null;
  let focoCumpridoMs = 0;
  for (const [indice, fase] of fases.entries()) {
    const fim = fase.inicioNoPlanoMs + fase.duracaoMs;
    if (t < fim) {
      if (fase.tipo === "foco") focoCumpridoMs += t - fase.inicioNoPlanoMs;
      return { fase, indice, restanteMs: fim - t, focoCumpridoMs, terminado: false, pausado };
    }
    if (fase.tipo === "foco") focoCumpridoMs += fase.duracaoMs;
  }
  return { fase: null, indice: fases.length, restanteMs: 0, focoCumpridoMs, terminado: true, pausado };
}

/** Pausas PLANEJADAS que já terminaram, com os instantes reais (para o servidor). */
export function pausasPlanejadasConcluidas(
  fases: Fase[],
  e: EstadoTimer,
  agora: number,
): { indice: number; tipo: "curta" | "longa"; inicioMs: number; fimMs: number }[] {
  const t = tempoDePlano(e, agora);
  return fases.flatMap((fase, indice) => {
    if (fase.tipo === "foco" || fase.inicioNoPlanoMs + fase.duracaoMs > t) return [];
    return [{
      indice,
      tipo: fase.tipo === "pausa_longa" ? ("longa" as const) : ("curta" as const),
      inicioMs: noRelogio(e, fase.inicioNoPlanoMs),
      fimMs: noRelogio(e, fase.inicioNoPlanoMs + fase.duracaoMs, true),
    }];
  });
}

/**
 * Concluída se o foco planejado foi cumprido; abandonada se parou antes. (Encerrar
 * durante a última pausa não existe: depois do último foco o plano já acabou.)
 */
export function statusAoEncerrar(fases: Fase[], c: Config, e: EstadoTimer, agora: number): "concluida" | "abandonada" {
  return momento(fases, e, agora).focoCumpridoMs >= focoPlanejadoMs(c) ? "concluida" : "abandonada";
}

/** "24:59", "1:05:00" */
export function relogio(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const dois = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${dois(m)}:${dois(s)}` : `${dois(m)}:${dois(s)}`;
}
