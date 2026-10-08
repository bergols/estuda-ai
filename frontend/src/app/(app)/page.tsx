import type { Metadata } from "next";

import { ListaDisciplinas } from "./lista-disciplinas";

export const metadata: Metadata = { title: "Disciplinas" };

export default function PaginaDisciplinas() {
  return <ListaDisciplinas />;
}
