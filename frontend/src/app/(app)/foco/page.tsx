import type { Metadata } from "next";

import { Foco } from "./foco";

export const metadata: Metadata = { title: "Foco" };

export default function PaginaFoco() {
  return <Foco />;
}
