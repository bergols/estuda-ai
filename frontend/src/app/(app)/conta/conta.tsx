"use client";

import { Tabela } from "@/components/graficos";
import { BotaoTema } from "@/components/tema";
import { Botao, Cabecalho, Carregando, Erro, Secao, Vazio } from "@/components/ui";
import { useEu, useGastos, useSair, useSairDeTodos } from "@/lib/consultas";
import { data, mes, usd } from "@/lib/formato";

function irParaLogin() {
  // Recarga completa: zera o cache do TanStack Query (dados do usuário que saiu)
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign("/login");
}

export function Conta() {
  const eu = useEu();
  const gastos = useGastos();
  const sair = useSair();
  const sairDeTodos = useSairDeTodos();

  return (
    <>
      <Cabecalho sobre="Conta" titulo={eu.data?.nome ?? " "} />
      {eu.isError && <Erro erro={eu.error} />}
      {eu.data && (
        <dl className="mb-10 grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-apagado">E-mail</dt>
          <dd>{eu.data.email}</dd>
          <dt className="text-apagado">Fuso horário</dt>
          <dd>{eu.data.fuso_horario}</dd>
          <dt className="text-apagado">Desde</dt>
          <dd>{data(eu.data.criado_em)}</dd>
          <dt className="text-apagado">Aparência</dt>
          <dd>
            <BotaoTema />
          </dd>
        </dl>
      )}

      <Secao titulo="Sessão">
        <div className="flex flex-wrap gap-4">
          <Botao variante="secundario" carregando={sair.isPending} onClick={() => sair.mutate(undefined, { onSuccess: irParaLogin })}>
            Sair deste aparelho
          </Botao>
          <Botao
            variante="secundario"
            carregando={sairDeTodos.isPending}
            onClick={() => {
              if (confirm("Desconectar todos os aparelhos (celular, outros navegadores)?")) {
                sairDeTodos.mutate(undefined, { onSuccess: irParaLogin });
              }
            }}
          >
            Sair de todos os aparelhos
          </Botao>
        </div>
        <p className="mt-3 text-xs text-apagado">
          O login vale 30 dias. &quot;Sair de todos&quot; invalida na hora todos os logins abertos, inclusive os de um
          aparelho perdido.
        </p>
        {sairDeTodos.isError && <Erro erro={sairDeTodos.error} />}
      </Secao>

      <Secao titulo="Gasto com IA por disciplina">
        {gastos.isPending && <Carregando />}
        {gastos.isError && <Erro erro={gastos.error} tentarDeNovo={() => gastos.refetch()} />}
        {gastos.data?.length === 0 && <Vazio>Nenhuma geração com IA ainda.</Vazio>}
        {gastos.data && gastos.data.length > 0 && (
          <Tabela
            cabecalho={["Mês", "Disciplina", "Gerações", "Custo"]}
            linhas={gastos.data.map((g) => [
              mes(g.mes),
              g.disciplina_nome ?? "(disciplina apagada)",
              g.geracoes,
              usd(g.custo_usd),
            ])}
          />
        )}
      </Secao>
    </>
  );
}
