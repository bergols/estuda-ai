import type { Metadata } from "next";

import { Painel } from "./painel";

export const metadata: Metadata = { title: "Painel" };

export default function PaginaPainel() {
  return <Painel />;
}
