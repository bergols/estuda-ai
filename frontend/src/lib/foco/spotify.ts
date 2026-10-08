import { invoke } from "@tauri-apps/api/core";

/**
 * Ponte para o Spotify do app desktop (desktop/src-tauri/src/spotify.rs). O Rust pega o
 * access token no backend e fala direto com o Spotify deste computador.
 */

export type ErroPlayer =
  | { tipo: "token_expirado" | "spotify_fechado" | "sem_dispositivo_ativo" | "sem_premium" | "recusado" }
  | { tipo: "nao_conectado" | "sem_conexao" }
  | { tipo: "limite"; espera_s: number; cota: boolean }
  | { tipo: "outro"; mensagem: string };

export type Tocando = {
  tocando: boolean;
  musica: string;
  artistas: string;
  capa: string | null;
  progresso_ms: number;
  duracao_ms: number;
};

/** Mensagem para a tela (o erro do Rust já vem classificado). */
export function mensagemDoErro(erro: unknown): string {
  const e = erro as ErroPlayer | string;
  if (typeof e === "string") return e;
  switch (e?.tipo) {
    case "spotify_fechado":
      return "Abra o Spotify neste computador (tentei abrir, mas ele não apareceu).";
    case "sem_dispositivo_ativo":
      return "O Spotify não achou onde tocar: dê play uma vez no app do Spotify.";
    case "sem_premium":
      return "Controlar o Spotify por outro app exige conta Premium.";
    case "nao_conectado":
      return "O Spotify não está conectado (ou a autorização foi retirada).";
    case "sem_conexao":
      return "Sem conexão com o Spotify agora.";
    case "limite":
      return e.cota
        ? "A cota diária do app no Spotify acabou; a música volta amanhã."
        : `Muitas chamadas ao Spotify; tento de novo em ${e.espera_s} s.`;
    case "token_expirado":
      return "O Spotify recusou o acesso; conecte de novo.";
    case "outro":
      return e.mensagem;
    default:
      return "O Spotify não respondeu como esperado.";
  }
}

export const conectarSpotify = () => invoke<void>("spotify_conectar");
export const tocar = (contexto: string) => invoke<void>("spotify_tocar", { contexto });
export const pausar = () => invoke<void>("spotify_pausar");
export const retomar = () => invoke<void>("spotify_retomar");
export const atual = () => invoke<Tocando | null>("spotify_atual");
