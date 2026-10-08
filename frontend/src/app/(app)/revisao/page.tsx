import type { Metadata } from "next";

import { Revisao } from "./revisao";

export const metadata: Metadata = { title: "Revisão do dia" };

export default function PaginaRevisao() {
  return <Revisao />;
}
