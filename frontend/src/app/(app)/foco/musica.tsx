"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Botao, Carregando, Erro } from "@/components/ui";
import type { Esquemas } from "@/lib/api";
import {
  chaves,
  useDesconectarSpotify,
  useDisciplinas,
  useMinhasPlaylists,
  useSalvarSpotify,
  useSpotify,
} from "@/lib/consultas";
import { conectarSpotify, mensagemDoErro } from "@/lib/foco/spotify";
import type { Metodo } from "@/lib/foco/timer";
import { DESKTOP } from "@/lib/plataforma";

import { NOMES_METODO } from "./configuracao";

type Playlist = Esquemas["SpotifyPlaylistConfig"];
type Escolha = { uri: string; nome: string };

const SELECT =
  "w-full border-0 border-b border-fio bg-transparent px-0 py-1.5 text-sm text-tinta focus:border-acento focus:outline-none focus:ring-0";

/** Uma chave por alvo: "padrao", "intervalo", "metodo:pomodoro", "disciplina:7". */
function chaveDe(p: Pick<Playlist, "alvo" | "metodo" | "disciplina_id">): string {
  if (p.alvo === "metodo") return `metodo:${p.metodo}`;
  if (p.alvo === "disciplina") return `disciplina:${p.disciplina_id}`;
  return p.alvo;
}

function playlistDe(chave: string, escolha: Escolha): Playlist {
  const [alvo, valor] = chave.split(":");
  if (alvo === "metodo") return { alvo: "metodo", metodo: valor as Metodo, ...escolha };
  if (alvo === "disciplina") return { alvo: "disciplina", disciplina_id: Number(valor), ...escolha };
  return { alvo: alvo as "padrao" | "intervalo", ...escolha };
}

export function Musica() {
  const spotify = useSpotify();
  if (spotify.isPending) return <Carregando />;
  if (spotify.isError) return <Erro erro={spotify.error} tentarDeNovo={() => spotify.refetch()} />;
  return spotify.data.conectado ? <Configurar estado={spotify.data} /> : <Conectar />;
}

function Conectar() {
  const cliente = useQueryClient();
  const [erro, setErro] = useState<string | null>(null);
  const [conectando, setConectando] = useState(false);

  if (!DESKTOP) {
    return <p className="text-sm text-apagado">Conecte o Spotify pelo app desktop (o navegador abre a autorização e volta para o app).</p>;
  }

  async function conectar() {
    setConectando(true);
    setErro(null);
    try {
      await conectarSpotify();
      await cliente.invalidateQueries({ queryKey: chaves.spotify });
    } catch (e) {
      setErro(mensagemDoErro(e));
    } finally {
      setConectando(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="max-w-prose text-sm text-apagado">
        Com o Spotify conectado, a sessão começa tocando a sua playlist de estudo, pausa ou troca no intervalo e
        retoma na volta. O navegador abre a autorização do Spotify; depois de aceitar, volte para cá.
      </p>
      {erro && <p role="alert" className="border-l-2 border-errado bg-alerta px-4 py-3 text-sm">{erro}</p>}
      <Botao onClick={conectar} carregando={conectando}>
        {conectando ? "Esperando a autorização no navegador…" : "Conectar o Spotify"}
      </Botao>
    </div>
  );
}

function Configurar({ estado }: { estado: Esquemas["SpotifyEstado"] }) {
  const playlists = useMinhasPlaylists(true);
  const disciplinas = useDisciplinas();
  const salvar = useSalvarSpotify();
  const desconectar = useDesconectarSpotify();
  const [noIntervalo, setNoIntervalo] = useState(estado.no_intervalo);
  const [escolhas, setEscolhas] = useState<Record<string, Escolha>>(() =>
    Object.fromEntries(estado.playlists.map((p) => [chaveDe(p), { uri: p.uri, nome: p.nome }])),
  );

  // As opções: as playlists da conta + as já escolhidas que não estão nela (ex.: de outra pessoa)
  const opcoes = new Map<string, string>();
  for (const p of playlists.data ?? []) opcoes.set(p.uri, p.nome);
  for (const e of Object.values(escolhas)) if (!opcoes.has(e.uri)) opcoes.set(e.uri, e.nome);

  function escolher(chave: string, uri: string) {
    setEscolhas((atual) => {
      const nova = { ...atual };
      if (uri) nova[chave] = { uri, nome: opcoes.get(uri) ?? uri };
      else delete nova[chave];
      return nova;
    });
  }

  // Função que devolve JSX, e não um componente: um componente definido aqui dentro seria
  // um tipo NOVO a cada render, e o React desmontaria o select (perdendo o foco)
  const linha = (chave: string, rotulo: string, dica?: string) => (
      <label key={chave} className="grid grid-cols-1 items-baseline gap-1 sm:grid-cols-[12rem_1fr] sm:gap-4">
        <span className="text-sm">
          {rotulo}
          {dica && <span className="block text-xs text-apagado">{dica}</span>}
        </span>
        <select className={SELECT} value={escolhas[chave]?.uri ?? ""} onChange={(e) => escolher(chave, e.target.value)}>
          <option value="">—</option>
          {[...opcoes].map(([uri, nome]) => (
            <option key={uri} value={uri}>{nome}</option>
          ))}
        </select>
      </label>
  );

  return (
    <div className="space-y-8">
      <p className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span>
          Conectado como <strong>{estado.nome ?? estado.spotify_id}</strong>
        </span>
        <Botao variante="discreto" carregando={desconectar.isPending} onClick={() => desconectar.mutate()}>
          desconectar
        </Botao>
      </p>
      {playlists.isError && <Erro erro={playlists.error} tentarDeNovo={() => playlists.refetch()} />}

      <fieldset className="space-y-3">
        <legend className="rotulo mb-2">Playlists</legend>
        {linha("padrao", "Padrão", "quando não há uma mais específica")}
        {(Object.keys(NOMES_METODO) as Metodo[]).map((m) => linha(`metodo:${m}`, NOMES_METODO[m]))}
        {disciplinas.data?.map((d) => linha(`disciplina:${d.id}`, d.nome, "disciplina: vale mais que o método"))}
        {linha("intervalo", "Intervalo", "se escolher trocar a música na pausa")}
      </fieldset>

      <fieldset>
        <legend className="rotulo mb-2">No intervalo</legend>
        <div role="radiogroup" className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {(
            [
              ["pausar", "pausar a música"],
              ["trocar", "trocar para a do intervalo"],
              ["continuar", "continuar tocando"],
            ] as const
          ).map(([valor, rotulo]) => (
            <label key={valor} className="flex items-center gap-2">
              <input type="radio" name="no-intervalo" checked={noIntervalo === valor} onChange={() => setNoIntervalo(valor)}
                className="accent-acento" />
              {rotulo}
            </label>
          ))}
        </div>
      </fieldset>

      {salvar.isError && <Erro erro={salvar.error} />}
      <div className="flex items-center gap-4">
        <Botao
          carregando={salvar.isPending}
          onClick={() =>
            salvar.mutate({
              no_intervalo: noIntervalo,
              playlists: Object.entries(escolhas).map(([chave, e]) => playlistDe(chave, e)),
            })
          }
        >
          Salvar música
        </Botao>
        {salvar.isSuccess && <span className="text-sm text-apagado">salvo</span>}
      </div>
    </div>
  );
}
