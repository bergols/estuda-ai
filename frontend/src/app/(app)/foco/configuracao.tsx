"use client";

import { useState, type FormEvent } from "react";

import { Botao, Campo } from "@/components/ui";
import { useDisciplinas } from "@/lib/consultas";
import { type Config, type Metodo, PADROES, duracaoDoPlano, erroDaConfig, plano } from "@/lib/foco/timer";
import { duracao } from "@/lib/formato";

export const NOMES_METODO: Record<Metodo, string> = {
  pomodoro: "Pomodoro",
  bloco: "Bloco contínuo",
  "52_17": "52/17",
  personalizado: "Personalizado",
};

const EXPLICACAO: Record<Metodo, string> = {
  pomodoro: "Blocos curtos de foco com pausas; uma pausa longa a cada alguns ciclos.",
  bloco: "Um bloco só, sem pausas, até a meta de tempo.",
  "52_17": "52 minutos de foco e 17 de pausa, em ciclos.",
  personalizado: "Você escolhe o foco, a pausa e quantos ciclos.",
};

export type Escolha = { config: Config; disciplinaId: number | null; meta: string | null };

const GUARDADA = "estuda-ai:ultima-sessao";

/** Última configuração usada (conveniência local; sem ela, os padrões). */
function escolhaGuardada(): Escolha {
  try {
    const salva = JSON.parse(localStorage.getItem(GUARDADA) ?? "null") as Escolha | null;
    if (salva && !erroDaConfig(salva.config)) return { ...salva, meta: null };
  } catch {
    // armazenamento indisponível ou corrompido: segue com os padrões
  }
  return { config: PADROES.pomodoro, disciplinaId: null, meta: null };
}

function guardar(escolha: Escolha) {
  try {
    localStorage.setItem(GUARDADA, JSON.stringify(escolha));
  } catch {
    // sem armazenamento: só não lembra da próxima vez
  }
}

const SELECT =
  "w-full border-0 border-b border-fio bg-transparent px-0 py-2 text-base text-tinta focus:border-acento focus:outline-none focus:ring-0";

export function Configuracao({ aoComecar }: { aoComecar: (e: Escolha) => void }) {
  const [inicial] = useState(escolhaGuardada);
  const [config, setConfig] = useState<Config>(inicial.config);
  const [disciplinaId, setDisciplinaId] = useState<number | null>(inicial.disciplinaId);
  const [meta, setMeta] = useState("");
  const disciplinas = useDisciplinas();

  const erro = erroDaConfig(config);
  const total = duracaoDoPlano(plano(config)) / 1000;

  function trocarMetodo(m: Metodo) {
    setConfig(m === config.metodo ? config : PADROES[m]);
  }

  function numero(campo: keyof Config, valor: string) {
    setConfig({ ...config, [campo]: valor === "" ? NaN : Number(valor) });
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (erro) return;
    const escolha = { config, disciplinaId, meta: meta.trim() || null };
    guardar(escolha);
    aoComecar(escolha);
  }

  const m = config.metodo;
  return (
    <form onSubmit={enviar} className="space-y-8">
      <fieldset>
        <legend className="rotulo mb-2">Método</legend>
        <div role="radiogroup" className="flex flex-wrap gap-x-6 gap-y-2 border-b border-fio">
          {(Object.keys(NOMES_METODO) as Metodo[]).map((metodo) => (
            <button
              key={metodo}
              type="button"
              role="radio"
              aria-checked={m === metodo}
              onClick={() => trocarMetodo(metodo)}
              className={`-mb-px border-b-2 py-2 text-sm ${
                m === metodo ? "border-acento text-tinta" : "border-transparent text-apagado hover:text-tinta"
              }`}
            >
              {NOMES_METODO[metodo]}
            </button>
          ))}
        </div>
        <p className="mt-2 text-sm text-apagado">{EXPLICACAO[m]}</p>
      </fieldset>

      <div className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3">
        {m === "bloco" ? (
          <Campo rotulo="Duração (min)" type="number" min={1} max={240} value={config.focoMin || ""}
            onChange={(e) => numero("focoMin", e.target.value)} />
        ) : (
          <>
            {m !== "52_17" && (
              <>
                <Campo rotulo="Foco (min)" type="number" min={1} max={240} value={config.focoMin || ""}
                  onChange={(e) => numero("focoMin", e.target.value)} />
                <Campo rotulo="Pausa (min)" type="number" min={0} max={60} value={Number.isNaN(config.pausaMin) ? "" : config.pausaMin}
                  onChange={(e) => numero("pausaMin", e.target.value)} />
              </>
            )}
            <Campo rotulo="Ciclos" type="number" min={1} max={12} value={config.ciclos || ""}
              onChange={(e) => numero("ciclos", e.target.value)} />
            {m === "pomodoro" && (
              <>
                <Campo rotulo="Pausa longa (min)" type="number" min={1} max={90} value={config.pausaLongaMin ?? ""}
                  onChange={(e) => numero("pausaLongaMin", e.target.value)} />
                <Campo rotulo="Longa a cada (ciclos)" type="number" min={2} max={12} value={config.ciclosAtePausaLonga ?? ""}
                  onChange={(e) => numero("ciclosAtePausaLonga", e.target.value)} />
              </>
            )}
          </>
        )}
      </div>

      <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
        <label className="block">
          <span className="rotulo">Disciplina</span>
          <select className={SELECT} value={disciplinaId ?? ""}
            onChange={(e) => setDisciplinaId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Sem disciplina</option>
            {disciplinas.data?.map((d) => (
              <option key={d.id} value={d.id}>{d.nome}</option>
            ))}
          </select>
        </label>
        <Campo rotulo="Meta (opcional)" placeholder="ex.: terminar a lista 3" maxLength={200} value={meta}
          onChange={(e) => setMeta(e.target.value)} />
      </div>

      {erro && (
        <p role="alert" className="border-l-2 border-errado bg-alerta px-4 py-3 text-sm">{erro}</p>
      )}
      <div className="flex flex-wrap items-center gap-4">
        <Botao type="submit" disabled={!!erro}>Começar sessão</Botao>
        {!erro && <span className="text-sm text-apagado">{duracao(total)} no total, com as pausas</span>}
      </div>
    </form>
  );
}
