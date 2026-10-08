/**
 * Um "plim" curto ao mudar de fase (fim do foco, fim da pausa), gerado na hora com a
 * Web Audio API: sem arquivo de som para baixar nem empacotar.
 */
export function tocarAviso() {
  try {
    const ctx = new AudioContext();
    const tom = ctx.createOscillator();
    const volume = ctx.createGain();
    tom.frequency.value = 660;
    volume.gain.setValueAtTime(0.0001, ctx.currentTime);
    volume.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02);
    volume.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.9);
    tom.connect(volume).connect(ctx.destination);
    tom.start();
    tom.stop(ctx.currentTime + 0.9);
    tom.onended = () => ctx.close();
  } catch {
    // sem áudio (permissão, dispositivo): a mudança de fase continua visível na tela
  }
}
