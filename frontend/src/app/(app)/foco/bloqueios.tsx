"use client";

import { useState } from "react";

import { AreaTexto, Botao, Campo, Carregando, Erro } from "@/components/ui";
import { useBloqueios, useSalvarBloqueios } from "@/lib/consultas";
import { type Bloqueios, linhas } from "@/lib/foco/bloqueio";

/** Configuração dos bloqueios (vale nos dois computadores: fica no servidor). */
export function ConfigBloqueios() {
  const bloqueios = useBloqueios();
  if (bloqueios.isPending) return <Carregando />;
  if (bloqueios.isError) return <Erro erro={bloqueios.error} tentarDeNovo={() => bloqueios.refetch()} />;
  return <Formulario inicial={bloqueios.data} />;
}

function Formulario({ inicial }: { inicial: Bloqueios }) {
  const salvar = useSalvarBloqueios();
  const [sitesLigado, setSitesLigado] = useState(inicial.bloquear_sites);
  const [programasLigado, setProgramasLigado] = useState(inicial.bloquear_programas);
  const [espera, setEspera] = useState(String(inicial.espera_emergencia_s));
  const [sites, setSites] = useState((inicial.sites ?? []).join("\n"));
  const [mac, setMac] = useState((inicial.programas?.macos ?? []).join("\n"));
  const [windows, setWindows] = useState((inicial.programas?.windows ?? []).join("\n"));

  function enviar(e: React.FormEvent) {
    e.preventDefault();
    salvar.mutate(
      {
        bloquear_sites: sitesLigado,
        bloquear_programas: programasLigado,
        espera_emergencia_s: Number(espera),
        sites: linhas(sites),
        programas: { macos: linhas(mac), windows: linhas(windows) },
      },
      {
        // Mostra o que o servidor guardou ("https://www.youtube.com/x" vira "youtube.com")
        onSuccess: (b) => {
          setSites((b.sites ?? []).join("\n"));
          setMac((b.programas?.macos ?? []).join("\n"));
          setWindows((b.programas?.windows ?? []).join("\n"));
        },
      },
    );
  }

  return (
    <form onSubmit={enviar} className="space-y-8">
      <fieldset className="space-y-3">
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 accent-acento" checked={sitesLigado}
            onChange={(e) => setSitesLigado(e.target.checked)} />
          <span>
            Bloquear sites durante a sessão
            <span className="block text-xs text-apagado">
              Pede a senha do computador uma vez por sessão. O bloqueio sai sozinho no fim, se o app fechar ou se o
              computador reiniciar.
            </span>
          </span>
        </label>
        <AreaTexto rotulo="Sites (um por linha)" rows={4} value={sites} onChange={(e) => setSites(e.target.value)}
          placeholder={"youtube.com\ninstagram.com"} spellCheck={false} />
      </fieldset>

      <fieldset className="space-y-3">
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 accent-acento" checked={programasLigado}
            onChange={(e) => setProgramasLigado(e.target.checked)} />
          <span>
            Fechar programas durante a sessão
            <span className="block text-xs text-apagado">
              Se abrir um da lista, o app fecha na hora e conta como interrupção. O Spotify, o Finder e o Explorer
              nunca são fechados.
            </span>
          </span>
        </label>
        <div className="grid gap-6 sm:grid-cols-2">
          <AreaTexto rotulo="No Mac (nome do app)" rows={3} value={mac} onChange={(e) => setMac(e.target.value)}
            placeholder={"Discord\nSteam"} spellCheck={false} />
          <AreaTexto rotulo="No Windows (nome do .exe)" rows={3} value={windows}
            onChange={(e) => setWindows(e.target.value)} placeholder={"Discord.exe\nsteam.exe"} spellCheck={false} />
        </div>
      </fieldset>

      <Campo rotulo="Saída de emergência: esperar (segundos)" type="number" min={10} max={600} value={espera}
        onChange={(e) => setEspera(e.target.value)} className="max-w-xs"
        dica="Para encerrar uma sessão com bloqueio, o app espera este tempo antes de liberar." />

      {salvar.isError && <Erro erro={salvar.error} />}
      <div className="flex items-center gap-4">
        <Botao type="submit" carregando={salvar.isPending}>Salvar bloqueios</Botao>
        {salvar.isSuccess && <span className="text-sm text-apagado">salvo</span>}
      </div>
    </form>
  );
}
