// Roda o Next com ALVO=desktop: "node scripts/alvo-desktop.mjs build" ou "... dev -p 3001".
//
// Por que um script e não "ALVO=desktop next build" no package.json? Essa sintaxe de
// variável é do shell do Mac/Linux; o build do Windows (CI e o seu PC) roda os scripts
// do npm no cmd.exe, onde ela não existe. Node funciona igual nos dois.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const next = createRequire(import.meta.url).resolve("next/dist/bin/next");
const filho = spawn(process.execPath, [next, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, ALVO: "desktop" },
});
filho.on("exit", (codigo, sinal) => process.exit(sinal ? 1 : (codigo ?? 1)));
