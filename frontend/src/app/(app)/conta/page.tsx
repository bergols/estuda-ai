import type { Metadata } from "next";

import { Conta } from "./conta";

export const metadata: Metadata = { title: "Conta" };

export default function PaginaConta() {
  return <Conta />;
}
