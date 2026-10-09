"use client";

import { useEffect, useRef, useState } from "react";

import { useBloqueios } from "@/lib/consultas";
import {
  type Sistema,
  bloquearSites,
  bloqueiosLembrados,
  escutarProgramaFechado,
  iniciarProgramas,
  liberarSites,
  minutosDoBloqueio,
  oQueBloquear,
  pararProgramas,
  restaurarSites,
  situacaoSites,
} from "@/lib/foco/bloqueio";
import { configDe, registrarEvento, type SessaoAtiva } from "@/lib/foco/sessao";
import { plano } from "@/lib/foco/timer";

import { novaChave } from "./usar-sessao";

type Sites = "nao" | "pedindo" | "ativo";

/**
 * Liga os bloqueios quando a sessão fica ativa e desliga quando ela acaba (por
 * qualquer caminho: fim do plano, encerrar, saída de emergência). A configuração é
 * lida UMA vez, no começo: mudar a lista no meio não pede a senha de novo.
 */
export function useBloqueio(
  ativa: boolean,
  sessao: SessaoAtiva | null,
  mudar: (f: (s: SessaoAtiva) => SessaoAtiva | null) => void,
  sistema: Sistema,
) {
  const bloqueios = useBloqueios();
  const [sites, setSites] = useState<Sites>("nao");
  const [programas, setProgramas] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [travado, setTravado] = useState(false);
  const [fechado, setFechado] = useState<string | null>(null);

  // Refs: o efeito abaixo depende só de `ativa` (mudar a configuração ou o sistema no
  // meio da sessão não pode desbloquear e bloquear de novo, pedindo a senha outra vez)
  const contexto = useRef({ sessao, sistema, config: bloqueios.data ?? null });
  useEffect(() => {
    contexto.current = { sessao, sistema, config: bloqueios.data ?? null };
  }, [sessao, sistema, bloqueios.data]);

  // Ao abrir o app: ficou bloqueio de sites sem guardião (sessão que travou feio)?
  useEffect(() => {
    situacaoSites().then((s) => s.bloqueado && !s.guardiao_vivo && setTravado(true), () => {});
  }, []);

  useEffect(() => {
    if (!ativa) return;
    const { sessao: s, sistema: so, config } = contexto.current;
    const alvo = oQueBloquear(config ?? bloqueiosLembrados(), so);
    let vivo = true;

    if (alvo.programas.length > 0) {
      iniciarProgramas(alvo.programas).then(() => vivo && setProgramas(true), () => {});
    }
    if (alvo.sites.length > 0 && s) {
      setSites("pedindo");
      const minutos = minutosDoBloqueio(plano(configDe(s.dados)), s.local.timer, Date.now());
      bloquearSites(alvo.sites, minutos).then(
        () => {
          // A sessão acabou enquanto a janela de senha estava aberta: libera já
          if (!vivo) liberarSites().then((ok) => !ok && setTravado(true), () => setTravado(true));
          else setSites("ativo");
        },
        (erro) => {
          if (!vivo) return;
          setSites("nao");
          setAviso(
            String(erro) === "cancelado"
              ? "Sites sem bloqueio nesta sessão: a senha não foi digitada."
              : `Sites sem bloqueio nesta sessão: ${String(erro)}`,
          );
        },
      );
    }

    const parar = escutarProgramaFechado((programa) => {
      mudar((x) => registrarEvento(x, "programa_bloqueado", Date.now(), novaChave, programa.slice(0, 200)));
      setFechado(programa);
    });

    return () => {
      vivo = false;
      parar.then((f) => f(), () => {});
      pararProgramas().catch(() => {});
      if (alvo.sites.length > 0) {
        liberarSites().then((ok) => !ok && setTravado(true), () => setTravado(true));
      }
      setSites("nao");
      setProgramas(false);
      setAviso(null);
      setFechado(null);
    };
  }, [ativa, mudar]);

  // O aviso "fechei o X" some sozinho
  useEffect(() => {
    if (!fechado) return;
    const id = setTimeout(() => setFechado(null), 5_000);
    return () => clearTimeout(id);
  }, [fechado]);

  const config = bloqueios.data ?? bloqueiosLembrados();
  return {
    /** Há algo bloqueado agora (vale a saída de emergência) */
    bloqueando: programas || sites !== "nao",
    esperaS: config?.espera_emergencia_s ?? 60,
    aviso,
    fechado,
    travado,
    async desbloquear() {
      await restaurarSites();
      setTravado(false);
    },
  };
}
