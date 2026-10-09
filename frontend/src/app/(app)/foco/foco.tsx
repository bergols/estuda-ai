"use client";

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useCallback, useEffect, useRef, useState } from "react";

import { Botao, Cabecalho, Carregando, Secao } from "@/components/ui";
import { useDisciplinas } from "@/lib/consultas";
import { tocarAviso } from "@/lib/foco/aviso";
import type { Momento } from "@/lib/foco/musica";
import { sessaoEmAndamento } from "@/lib/foco/local";
import {
  continuarAposFechamento,
  encerrar,
  encerrarAposFechamento,
  novaSessao,
  pausarSessao,
  registrarEvento,
  retomarSessao,
  saiuDaJanela,
  ultimoSinal,
  voltouParaJanela,
  type SessaoAtiva,
  type SessaoEnvio,
} from "@/lib/foco/sessao";
import { duracao, hora } from "@/lib/formato";
import { DESKTOP } from "@/lib/plataforma";

import { NOMES_METODO, Configuracao, type Escolha } from "./configuracao";
import { Historico } from "./historico";
import { ConfigBloqueios } from "./bloqueios";
import { Musica } from "./musica";
import { RodapeSincronia } from "./rodape-sincronia";
import { TelaDeFoco } from "./tela-de-foco";
import { useBloqueio } from "./usar-bloqueio";
import { useMusica, usePlayer } from "./usar-musica";
import { novaChave, useRelogio, useSessao, useSituacaoSincronia } from "./usar-sessao";

export function Foco() {
  return DESKTOP ? <FocoDesktop /> : <FocoWeb />;
}

function FocoWeb() {
  return (
    <>
      <Cabecalho titulo="Foco" />
      <p className="mb-10 max-w-prose text-apagado">
        As sessões de estudo (pomodoro, bloco contínuo, 52/17) rodam no app desktop, com tela cheia e
        funcionando mesmo sem internet. Aqui aparece o histórico delas, depois de sincronizadas.
      </p>
      <Secao titulo="Últimas sessões">
        <Historico />
      </Secao>
      <Secao titulo="Música (Spotify)">
        <Musica />
      </Secao>
      <Secao titulo="Bloqueios">
        <ConfigBloqueios />
      </Secao>
    </>
  );
}

/** Antes de tudo, pergunta ao SQLite se ficou uma sessão aberta (app fechado ou travado). */
function FocoDesktop() {
  const [aberta, setAberta] = useState<SessaoAtiva | null | undefined>(undefined);
  useEffect(() => {
    sessaoEmAndamento().then(setAberta, () => setAberta(null));
  }, []);
  if (aberta === undefined) return <Carregando />;
  return <Painel aberta={aberta} />;
}

function Painel({ aberta }: { aberta: SessaoAtiva | null }) {
  const [recuperar, setRecuperar] = useState(aberta);
  const { sessao, mudar, comecar, erroAoGravar } = useSessao(null);
  const sincronia = useSituacaoSincronia();
  const disciplinas = useDisciplinas();
  const [sistema, setSistema] = useState<SessaoEnvio["sistema"]>("macos");
  const musica = useMusica();
  const ativa = sessao?.dados.status === "em_andamento";
  const player = usePlayer(ativa, musica.conectado, musica.setAviso);
  const bloqueio = useBloqueio(ativa, sessao, mudar, sistema);

  // A música de cada momento. A sessão atual fica numa ref para o callback do relógio
  // ser estável (senão o intervalo de 250 ms reiniciaria a cada render).
  const sessaoAtual = useRef(sessao);
  useEffect(() => {
    sessaoAtual.current = sessao;
  }, [sessao]);
  const musicaEm = useCallback(
    (momento: Momento, s: SessaoAtiva | null = sessaoAtual.current) => {
      if (s) {
        musica.executar(momento, { metodo: s.dados.metodo, disciplinaId: s.dados.disciplina_id ?? null },
          player.atualizar);
      }
    },
    [musica, player.atualizar],
  );
  const musicaRef = useRef(musicaEm);
  useEffect(() => {
    musicaRef.current = musicaEm;
  }, [musicaEm]);
  const agora = useRelogio(
    sessao,
    mudar,
    useCallback((de, para) => {
      tocarAviso();
      musicaRef.current(para ? { tipo: "fase", de: de.tipo, para: para.tipo } : { tipo: "fim" });
    }, []),
  );

  useEffect(() => {
    invoke<SessaoEnvio["sistema"]>("sistema").then(setSistema, () => {});
  }, []);

  // Tela cheia e por cima de tudo enquanto a sessão está ativa
  useEffect(() => {
    if (!ativa) return;
    invoke("modo_foco", { ativar: true }).catch(() => {});
    return () => {
      invoke("modo_foco", { ativar: false }).catch(() => {});
    };
  }, [ativa]);

  // Saiu da janela (outro app em primeiro plano) / voltou
  useEffect(() => {
    if (!ativa) return;
    const parar = getCurrentWindow().onFocusChanged(({ payload: focada }) => {
      const t = Date.now();
      mudar((s) => (focada ? voltouParaJanela(s, t, novaChave) : saiuDaJanela(s, t)));
    });
    return () => {
      parar.then((f) => f());
    };
  }, [ativa, mudar]);

  const nomeDisciplina = (id: number | null | undefined) =>
    id == null ? null : (disciplinas.data?.find((d) => d.id === id)?.nome ?? null);

  function comecarSessao(e: Escolha) {
    const nova = novaSessao({ ...e, sistema, agora: Date.now(), novaChave });
    comecar(nova);
    musicaEm({ tipo: "inicio" }, nova);
  }

  if (sessao && ativa) {
    return (
      <TelaDeFoco
        sessao={sessao}
        agora={agora}
        disciplina={nomeDisciplina(sessao.dados.disciplina_id)}
        sincronia={sincronia}
        player={player}
        avisoMusica={musica.aviso}
        bloqueio={bloqueio}
        aoPausar={() => {
          mudar((s) => pausarSessao(s, Date.now()));
          musicaEm({ tipo: "pausa_manual" });
        }}
        aoRetomar={() => {
          mudar((s) => retomarSessao(s, Date.now(), novaChave));
          musicaEm({ tipo: "retomada_manual" });
        }}
        aoEncerrar={() => {
          mudar((s) => encerrar(s, Date.now(), novaChave));
          musicaEm({ tipo: "fim" });
        }}
        aoSairEmergencia={(esperouS) => {
          const t = Date.now();
          mudar((s) => encerrar(registrarEvento(s, "saida_emergencia", t, novaChave, `esperou ${esperouS} s`), t, novaChave));
          musicaEm({ tipo: "fim" });
        }}
      />
    );
  }

  return (
    <>
      <Cabecalho titulo="Foco" acoes={<RodapeSincronia situacao={sincronia} />} />
      {bloqueio.travado && <BloqueioTravado aoDesbloquear={bloqueio.desbloquear} />}
      {erroAoGravar && (
        <p role="alert" className="mb-6 border-l-2 border-errado bg-alerta px-4 py-3 text-sm">
          Não consegui gravar a sessão no computador: {erroAoGravar}
        </p>
      )}

      {recuperar ? (
        <Recuperacao
          sessao={recuperar}
          aoContinuar={() => {
            const continuada = continuarAposFechamento(recuperar, Date.now(), novaChave);
            comecar(continuada);
            musicaEm({ tipo: "inicio" }, continuada);
            setRecuperar(null);
          }}
          aoEncerrar={() => {
            comecar(encerrarAposFechamento(recuperar, novaChave));
            setRecuperar(null);
          }}
        />
      ) : sessao ? (
        <Resumo sessao={sessao} aoVoltar={() => comecar(null)} />
      ) : (
        <Secao titulo="Nova sessão">
          <Configuracao aoComecar={comecarSessao} />
        </Secao>
      )}

      <Secao titulo="Música (Spotify)">
        <Musica />
      </Secao>

      <Secao titulo="Bloqueios">
        <ConfigBloqueios />
      </Secao>

      <Secao titulo="Últimas sessões">
        <Historico />
      </Secao>
    </>
  );
}

/** O bloqueio de sites ficou e o guardião não respondeu: desbloquear pede a senha. */
function BloqueioTravado({ aoDesbloquear }: { aoDesbloquear: () => Promise<void> }) {
  const [erro, setErro] = useState<string | null>(null);
  const [indo, setIndo] = useState(false);
  return (
    <div role="alert" className="mb-6 border-l-2 border-errado bg-alerta px-4 py-3 text-sm">
      <p className="mb-2">
        Os sites da sua lista continuam bloqueados e o guardião do bloqueio não respondeu. Desbloquear pede a senha
        do computador.
      </p>
      {erro && <p className="mb-2">Não deu certo: {erro}</p>}
      <Botao
        variante="secundario"
        carregando={indo}
        onClick={() => {
          setIndo(true);
          setErro(null);
          aoDesbloquear().catch((e) => setErro(String(e))).finally(() => setIndo(false));
        }}
      >
        Desbloquear agora
      </Botao>
    </div>
  );
}

function Recuperacao({ sessao, aoContinuar, aoEncerrar }: { sessao: SessaoAtiva; aoContinuar: () => void; aoEncerrar: () => void }) {
  return (
    <Secao titulo="Uma sessão ficou aberta">
      <p className="mb-4 max-w-prose">
        A sessão de <strong>{NOMES_METODO[sessao.dados.metodo]}</strong> começou às {hora(sessao.dados.iniciada_em)} e
        o app fechou às {hora(ultimoSinal(sessao))}. Continuar conta o tempo fechado como pausa; encerrar termina a
        sessão às {hora(ultimoSinal(sessao))}.
      </p>
      <div className="flex flex-wrap gap-3">
        <Botao onClick={aoContinuar}>Continuar</Botao>
        <Botao variante="secundario" onClick={aoEncerrar}>Encerrar</Botao>
      </div>
    </Secao>
  );
}

/** Os mesmos números da vw_sessoes_foco, calculados aqui (a sessão pode nem ter subido ainda). */
function numeros(d: SessaoEnvio) {
  const real = (Date.parse(d.terminada_em ?? d.iniciada_em) - Date.parse(d.iniciada_em)) / 1000;
  const pausas = (d.pausas ?? []).reduce((t, p) => t + (Date.parse(p.terminada_em) - Date.parse(p.iniciada_em)) / 1000, 0);
  const fora = (d.eventos ?? []).reduce((t, e) => t + (e.duracao_s ?? 0), 0);
  const interrupcoes = (d.eventos ?? []).filter((e) => e.tipo !== "saida_emergencia").length;
  return { efetivo: Math.max(real - pausas - fora, 0), planejado: d.foco_min * 60 * d.ciclos, pausas: d.pausas?.length ?? 0, interrupcoes };
}

function Resumo({ sessao, aoVoltar }: { sessao: SessaoAtiva; aoVoltar: () => void }) {
  const n = numeros(sessao.dados);
  const concluida = sessao.dados.status === "concluida";
  return (
    <Secao titulo={concluida ? "Sessão concluída" : "Sessão encerrada antes do fim"}>
      <dl className="mb-6 grid grid-cols-3 gap-4">
        <div>
          <dt className="rotulo">Foco efetivo</dt>
          <dd className="font-serif text-3xl">{duracao(n.efetivo)}</dd>
          <dd className="text-sm text-apagado">de {duracao(n.planejado)} planejados</dd>
        </div>
        <div>
          <dt className="rotulo">Pausas</dt>
          <dd className="font-serif text-3xl">{n.pausas}</dd>
        </div>
        <div>
          <dt className="rotulo">Interrupções</dt>
          <dd className="font-serif text-3xl">{n.interrupcoes}</dd>
        </div>
      </dl>
      <Botao onClick={aoVoltar}>Nova sessão</Botao>
    </Secao>
  );
}
