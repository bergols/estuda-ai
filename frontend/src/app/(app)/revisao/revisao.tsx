"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Botao, Cabecalho, Carregando, Erro, Vazio } from "@/components/ui";
import { ErroApi, type Esquemas } from "@/lib/api";
import { chaves, useDisciplinas, useFilaDoDia, useRevisar } from "@/lib/consultas";
import { dataCurta } from "@/lib/formato";

/** Escala do SM-2: abaixo de 3 o card volta ao começo (intervalo de 1 dia). */
const NOTAS = [
  { nota: 0, rotulo: "Branco", explica: "não lembrei nada" },
  { nota: 1, rotulo: "Errei", explica: "lembrei ao ver a resposta" },
  { nota: 2, rotulo: "Quase", explica: "errei, mas parecia fácil" },
  { nota: 3, rotulo: "Difícil", explica: "acertei com esforço" },
  { nota: 4, rotulo: "Bom", explica: "acertei com hesitação" },
  { nota: 5, rotulo: "Fácil", explica: "na ponta da língua" },
] as const;

type Card = Esquemas["CardDaFila"];

export function Revisao() {
  const [disciplinaId, setDisciplinaId] = useState<number | undefined>();
  const disciplinas = useDisciplinas();
  const fila = useFilaDoDia(disciplinaId);
  const cliente = useQueryClient();

  return (
    <>
      <Cabecalho
        sobre="Repetição espaçada"
        titulo="Revisão do dia"
        acoes={
          <select
            aria-label="Filtrar por disciplina"
            value={disciplinaId ?? ""}
            onChange={(e) => setDisciplinaId(e.target.value ? Number(e.target.value) : undefined)}
            className="max-w-48 border-0 border-b border-fio bg-transparent py-1 text-sm focus:border-acento focus:ring-0"
          >
            <option value="">todas as disciplinas</option>
            {disciplinas.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.nome}
              </option>
            ))}
          </select>
        }
      />
      {fila.isPending && <Carregando texto="Montando a fila" />}
      {fila.isError && <Erro erro={fila.error} tentarDeNovo={() => fila.refetch()} />}
      {fila.data && (
        <Sessao
          // key: trocar de disciplina (ou recarregar a fila) recomeça a sessão
          key={`${disciplinaId ?? "todas"}-${fila.dataUpdatedAt}`}
          cards={fila.data.cards}
          recomecar={() => cliente.invalidateQueries({ queryKey: chaves.fila })}
        />
      )}
    </>
  );
}

function Sessao({ cards, recomecar }: { cards: Card[]; recomecar: () => void }) {
  const revisar = useRevisar();
  const [posicao, setPosicao] = useState(0);
  const [virado, setVirado] = useState(false);
  const [ultimo, setUltimo] = useState<Esquemas["ResultadoRevisaoSaida"] | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const card = cards[posicao];

  function avancar() {
    setPosicao((p) => p + 1);
    setVirado(false);
  }

  function dar(nota: number) {
    if (!card || revisar.isPending) return;
    setAviso(null);
    revisar.mutate(
      { flashcardId: card.flashcard_id, nota, versao: card.versao },
      {
        onSuccess: (r) => {
          setUltimo(r);
          avancar();
        },
        onError: (erro) => {
          // 409: o card foi revisado em outra aba/aparelho depois que a fila foi montada
          // (controle otimista pela versão). Nada foi gravado aqui; seguimos para o próximo.
          if (erro instanceof ErroApi && erro.status === 409) {
            setAviso("Este card já tinha sido revisado em outra aba. Pulei para o próximo.");
            avancar();
          }
        },
      },
    );
  }

  // Atalhos: espaço vira o card; 0 a 5 dão a nota
  useEffect(() => {
    function tecla(evento: KeyboardEvent) {
      if (evento.target instanceof HTMLInputElement || evento.target instanceof HTMLSelectElement) return;
      if (evento.key === " " && !virado) {
        evento.preventDefault();
        setVirado(true);
      } else if (virado && /^[0-5]$/.test(evento.key)) {
        dar(Number(evento.key));
      }
    }
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  if (cards.length === 0) {
    return <Vazio>Nada para revisar hoje. Volte amanhã, ou gere flashcards novos numa disciplina.</Vazio>;
  }
  if (!card) {
    return (
      <div className="py-8">
        <p className="font-serif text-2xl">Fila do dia concluída: {cards.length} cards.</p>
        <Ultimo resultado={ultimo} />
        <Botao variante="secundario" className="mt-6" onClick={recomecar}>
          Ver se sobrou algum
        </Botao>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between text-sm text-apagado">
        <span>
          {card.disciplina_nome}
          {card.topico && ` · ${card.topico}`}
        </span>
        <span className="font-mono">
          {posicao + 1}/{cards.length}
        </span>
      </div>
      {/* Barra de progresso: um fio que vai ficando vinho */}
      <div className="mb-8 h-px bg-fio" aria-hidden>
        <div className="h-px bg-acento transition-all" style={{ width: `${(posicao / cards.length) * 100}%` }} />
      </div>

      <article className="min-h-48">
        <p className="font-serif text-2xl leading-snug sm:text-3xl">{card.frente}</p>
        {Number(card.atraso_dias) >= 1 && (
          <p className="mt-2 text-xs text-errado">atrasado {Math.floor(Number(card.atraso_dias))} dia(s)</p>
        )}
        {virado && <p className="mt-6 border-l-2 border-acento pl-4 text-lg leading-relaxed">{card.verso}</p>}
      </article>

      <div className="mt-8">
        {!virado ? (
          <Botao className="w-full sm:w-auto" onClick={() => setVirado(true)}>
            Mostrar resposta <span className="hidden text-xs opacity-60 sm:inline">(espaço)</span>
          </Botao>
        ) : (
          <>
            <p className="rotulo mb-3">Como foi?</p>
            <div className="grid grid-cols-3 gap-px border border-fio bg-fio sm:grid-cols-6">
              {NOTAS.map((n) => (
                <button
                  key={n.nota}
                  type="button"
                  disabled={revisar.isPending}
                  onClick={() => dar(n.nota)}
                  className="bg-papel px-2 py-3 text-left transition-colors hover:bg-tarja disabled:opacity-50"
                >
                  <span className="block text-sm font-medium">
                    <span className="mr-1 font-mono text-apagado">{n.nota}</span>
                    {n.rotulo}
                  </span>
                  <span className="block text-xs text-apagado">{n.explica}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {aviso && <p className="mt-4 text-sm text-apagado">{aviso}</p>}
      {revisar.isError && !(revisar.error instanceof ErroApi && revisar.error.status === 409) && (
        <Erro erro={revisar.error} />
      )}
      <Ultimo resultado={ultimo} />
    </div>
  );
}

function Ultimo({ resultado }: { resultado: Esquemas["ResultadoRevisaoSaida"] | null }) {
  if (!resultado) return null;
  const { anterior, novo } = resultado;
  return (
    <p className="mt-8 border-t border-fio pt-3 font-mono text-xs text-apagado">
      último card: intervalo {anterior.intervalo_dias}→{novo.intervalo_dias} d · facilidade {Number(anterior.facilidade).toFixed(2)}→
      {Number(novo.facilidade).toFixed(2)} · volta em {dataCurta(resultado.proxima_revisao)}
    </p>
  );
}
