//! Bloqueio do modo foco: sites (arquivo hosts) e programas.
//!
//! # Sites: o "guardião"
//!
//! O arquivo hosts só pode ser alterado pelo administrador. Em vez de um serviço de
//! administrador instalado para sempre (decisão do autor), o app pede a senha UMA vez
//! por sessão e roda um script curto como administrador, o guardião, que:
//!
//! 1. copia o hosts original (`hosts.antes`) e grava o que vai escrever
//!    (`hosts.bloqueado`) numa pasta do sistema que só o administrador altera;
//! 2. deixa uma tarefa que restaura no próximo boot (LaunchDaemon no Mac, tarefa
//!    agendada no Windows), para o caso de o computador desligar no meio;
//! 3. escreve o bloqueio e fica vigiando: libera quando o app pede (arquivo de sinal),
//!    quando o app fecha ou trava (o processo some) ou no horário-limite;
//! 4. ao liberar, se o hosts ainda é EXATAMENTE o que ele escreveu, devolve a cópia
//!    original byte a byte; se alguém mexeu no meio, tira só o bloco marcado.
//!
//! Nunca ficar com sites bloqueados é o requisito número 1: por isso são quatro
//! caminhos independentes de volta (sinal, processo, prazo, boot), e a restauração pode
//! rodar quantas vezes for (sem bloqueio, não faz nada).
//!
//! Segurança: o script roda como administrador, então tudo o que entra nele é
//! validado (domínios só com `[a-z0-9.-]`) e citado com as regras de cada linguagem. O
//! script nunca EXECUTA nem COPIA para o hosts um arquivo que o usuário comum possa
//! alterar: o texto vai dentro do próprio comando, e a pasta de estado é do sistema.
//!
//! Este módulo só GERA os scripts (funções puras); os testes os executam de verdade
//! contra um hosts falso (`sh` no Mac/Linux, PowerShell no Windows).

use base64::Engine;
use base64::engine::general_purpose::STANDARD;

pub const MARCA_INICIO: &str =
    "# >>> estuda-ai: bloqueio do modo foco (sai sozinho no fim da sessao) >>>";
pub const MARCA_FIM: &str = "# <<< estuda-ai <<<";
/// Nome do LaunchDaemon (Mac) e da tarefa agendada (Windows) que restauram no boot.
pub const ROTULO_RESTAURAR: &str = "io.github.bergols.estudaai.restaurar";
pub const TAREFA_WINDOWS: &str = "estuda-ai-restaurar";
pub const MAX_SITES: usize = 200;

// ------------------------------------------------------------------- domínios

/// Mesma regra do CHECK `ck_sites_bloqueados_dominio_valido` do banco: rótulos de 1 a
/// 63 caracteres (letra/dígito nas pontas, hífen no meio), TLD só de letras, até 253.
pub fn dominio_valido(d: &str) -> bool {
    if d.is_empty() || d.len() > 253 {
        return false;
    }
    let rotulos: Vec<&str> = d.split('.').collect();
    if rotulos.len() < 2 {
        return false;
    }
    let (tld, resto) = rotulos.split_last().expect("2 ou mais rótulos");
    let tld_ok = (2..=63).contains(&tld.len()) && tld.bytes().all(|b| b.is_ascii_lowercase());
    let rotulo_ok = |r: &&str| {
        let b = r.as_bytes();
        (1..=63).contains(&b.len())
            && b.iter()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
            && b[0] != b'-'
            && b[b.len() - 1] != b'-'
    };
    tld_ok && resto.iter().all(rotulo_ok)
}

/// Os domínios que vão para o hosts: só os válidos, sem repetição, no máximo
/// `MAX_SITES`. Um inválido é DESCARTADO (e não "consertado"): ele já passou pela API
/// e pelo banco, então chegar aqui inválido é sinal de algo errado.
pub fn dominios_para_bloquear(lista: &[String]) -> Vec<String> {
    let mut saida: Vec<String> = Vec::new();
    for d in lista {
        if dominio_valido(d) && !saida.contains(d) {
            saida.push(d.clone());
        }
        if saida.len() == MAX_SITES {
            break;
        }
    }
    saida
}

/// As linhas do bloco: IPv4 e IPv6, com e sem www (o navegador tenta os dois).
/// 0.0.0.0 (e não 127.0.0.1): endereço "nenhum lugar", falha na hora, sem esperar um
/// servidor local que não existe responder.
pub fn linhas_do_bloco(dominios: &[String]) -> Vec<String> {
    dominios
        .iter()
        .flat_map(|d| {
            [
                format!("0.0.0.0 {d}"),
                format!("0.0.0.0 www.{d}"),
                format!(":: {d}"),
                format!(":: www.{d}"),
            ]
        })
        .collect()
}

// --------------------------------------------------------------------- aspas

/// Texto entre aspas simples para o `sh`: dentro delas nada é especial, e a própria
/// aspa simples vira `'\''` (fecha, aspa escapada, abre de novo).
pub fn aspas_sh(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// Texto entre aspas duplas do AppleScript: `\` e `"` com escape, e a quebra de linha
/// como `\n` (o script do guardião tem várias; assim o código-fonte do AppleScript fica
/// numa linha só).
pub fn aspas_applescript(s: &str) -> String {
    format!(
        "\"{}\"",
        s.replace('\\', r"\\")
            .replace('"', "\\\"")
            .replace('\n', r"\n")
    )
}

/// Texto entre aspas simples do PowerShell: a aspa simples dobra.
pub fn aspas_ps(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

/// `-EncodedCommand` do PowerShell: base64 do texto em UTF-16LE. Evita uma camada
/// inteira de aspas na linha de comando do Windows.
pub fn comando_codificado_ps(script: &str) -> String {
    let bytes: Vec<u8> = script.encode_utf16().flat_map(u16::to_le_bytes).collect();
    STANDARD.encode(bytes)
}

// ------------------------------------------------------------------ guardião

/// Tudo o que o guardião precisa saber. `teste` = sem LaunchDaemon/tarefa agendada e
/// sem limpar o cache de DNS (os testes rodam sem administrador, contra um hosts falso).
#[derive(Debug, Clone)]
pub struct Guardiao {
    pub hosts: String,
    /// Pasta do sistema (só o administrador altera): cópias do hosts e o script
    pub estado: String,
    /// Arquivo cujo surgimento pede para liberar (na pasta do usuário: pedir para
    /// LIBERAR é a única coisa que o usuário comum consegue fazer com ele)
    pub sinal: String,
    /// Processo do app: se ele some (fechou, travou), o guardião libera
    pub pid: u32,
    /// Parte do nome do processo do app (protege contra o PID ser reaproveitado)
    pub nome_processo: String,
    /// Horário-limite (segundos Unix): libera mesmo se nada mais acontecer
    pub ate: u64,
    /// Identifica ESTE guardião: se outro assumir (sessão nova), este sai sem mexer
    pub token: String,
    pub dominios: Vec<String>,
    pub teste: bool,
}

const SCRIPT_SH: &str = r#"#!/bin/sh
# Guardião do bloqueio de sites do estuda-ai. Gerado pelo app; roda como administrador.
# Modos: aplicar | vigiar | restaurar. Explicação em docs/modo-foco.md (bloqueio de sites).
set -u
HOSTS=@HOSTS@
ESTADO=@ESTADO@
SINAL=@SINAL@
PID_APP=@PID@
NOME=@NOME@
ATE=@ATE@
TOKEN=@TOKEN@
TESTE=@TESTE@
DAEMON=@DAEMON@
INICIO=@INICIO@
FIM=@FIM@

limpar_cache() {
  [ "$TESTE" = 1 ] && return 0
  dscacheutil -flushcache 2>/dev/null
  killall -HUP mDNSResponder 2>/dev/null
  return 0
}

restaurar() {
  if [ -f "$ESTADO/hosts.bloqueado" ] && cmp -s "$HOSTS" "$ESTADO/hosts.bloqueado"; then
    # Ninguém mexeu: volta a cópia original, byte a byte
    cat "$ESTADO/hosts.antes" > "$HOSTS"
  elif grep -qxF "$INICIO" "$HOSTS" 2>/dev/null; then
    # Alguém editou o hosts durante a sessão: tira só o bloco, preserva o resto
    awk -v i="$INICIO" -v f="$FIM" '$0 == i { fora = 1; next } fora && $0 == f { fora = 0; next } !fora' \
      "$HOSTS" > "$ESTADO/hosts.tmp" && cat "$ESTADO/hosts.tmp" > "$HOSTS"
  fi
  rm -f "$ESTADO/hosts.antes" "$ESTADO/hosts.bloqueado" "$ESTADO/hosts.tmp" "$ESTADO/vivo" "$ESTADO/dono"
  limpar_cache
  if [ "$TESTE" != 1 ]; then
    rm -f "$DAEMON"
    # Por último: no boot, quem roda isto é o próprio daemon, e o bootout o encerra
    launchctl bootout system/@ROTULO@ 2>/dev/null
  fi
  return 0
}

aplicar() {
  mkdir -p "$ESTADO" && chmod 755 "$ESTADO" || exit 1
  restaurar # sobra de uma sessão anterior que travou
  cat "$HOSTS" > "$ESTADO/hosts.antes" || exit 1
  {
    cat "$ESTADO/hosts.antes"
    printf '\n%s\n' "$INICIO"
    for d in @DOMINIOS@; do
      printf '0.0.0.0 %s\n0.0.0.0 www.%s\n:: %s\n:: www.%s\n' "$d" "$d" "$d" "$d"
    done
    printf '%s\n' "$FIM"
  } > "$ESTADO/hosts.bloqueado" || exit 1
  printf '%s' "$TOKEN" > "$ESTADO/dono"
  if [ "$TESTE" != 1 ]; then
    cat > "$DAEMON" <<'FIM_DO_PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>@ROTULO@</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>@ESTADO_XML@/guardiao.sh</string><string>restaurar</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
FIM_DO_PLIST
    chown root:wheel "$DAEMON" && chmod 644 "$DAEMON"
  fi
  if ! cat "$ESTADO/hosts.bloqueado" > "$HOSTS"; then
    restaurar
    exit 1
  fi
  date +%s > "$ESTADO/vivo"
  limpar_cache
}

vigiar() {
  while :; do
    # Outro guardião assumiu (sessão nova): sai sem mexer no bloqueio dele
    [ "$(cat "$ESTADO/dono" 2>/dev/null)" = "$TOKEN" ] || exit 0
    [ -e "$SINAL" ] && break
    ps -p "$PID_APP" -o comm= 2>/dev/null | grep -q "$NOME" || break
    [ "$(date +%s)" -lt "$ATE" ] || break
    date +%s > "$ESTADO/vivo"
    sleep 2
  done
  restaurar
}

case "${1:-}" in
  aplicar) aplicar ;;
  vigiar) vigiar ;;
  restaurar) restaurar ;;
  *) echo "uso: guardiao.sh aplicar|vigiar|restaurar" >&2; exit 2 ;;
esac
"#;

impl Guardiao {
    fn daemon(&self) -> String {
        format!("/Library/LaunchDaemons/{ROTULO_RESTAURAR}.plist")
    }

    /// O script do guardião para o `sh` (macOS; nos testes, também Linux).
    pub fn script_sh(&self) -> String {
        let dominios: Vec<String> = self.dominios.iter().map(|d| aspas_sh(d)).collect();
        SCRIPT_SH
            .replace("@HOSTS@", &aspas_sh(&self.hosts))
            .replace("@ESTADO_XML@", &xml(&self.estado))
            .replace("@ESTADO@", &aspas_sh(&self.estado))
            .replace("@SINAL@", &aspas_sh(&self.sinal))
            .replace("@PID@", &self.pid.to_string())
            .replace("@NOME@", &aspas_sh(&self.nome_processo))
            .replace("@ATE@", &self.ate.to_string())
            .replace("@TOKEN@", &aspas_sh(&self.token))
            .replace("@TESTE@", if self.teste { "1" } else { "0" })
            .replace("@DAEMON@", &aspas_sh(&self.daemon()))
            .replace("@INICIO@", &aspas_sh(MARCA_INICIO))
            .replace("@FIM@", &aspas_sh(MARCA_FIM))
            .replace("@ROTULO@", ROTULO_RESTAURAR)
            .replace("@DOMINIOS@", &dominios.join(" "))
    }

    /// O comando que roda como administrador no Mac: grava o script na pasta do
    /// sistema (dentro do próprio comando, nunca lendo um arquivo do usuário), aplica,
    /// e deixa o vigia rodando em segundo plano. O `do shell script` só volta quando a
    /// saída do comando fecha; com o vigia redirecionado para /dev/null, volta logo
    /// depois do "aplicar".
    pub fn comando_mac(&self) -> String {
        let script = aspas_sh(&format!("{}/guardiao.sh", self.estado));
        self.gravar_script_mac(&format!(
            "/bin/sh {script} aplicar && (nohup /bin/sh {script} vigiar > /dev/null 2>&1 &)"
        ))
    }

    /// Só restaurar (o botão "desbloquear agora", quando o guardião não respondeu).
    pub fn comando_mac_restaurar(&self) -> String {
        let script = aspas_sh(&format!("{}/guardiao.sh", self.estado));
        self.gravar_script_mac(&format!("/bin/sh {script} restaurar"))
    }

    fn gravar_script_mac(&self, depois: &str) -> String {
        let estado = aspas_sh(&self.estado);
        let script = aspas_sh(&format!("{}/guardiao.sh", self.estado));
        format!(
            "mkdir -p {estado} && chmod 755 {estado} && cat > {script} <<'FIM_DO_GUARDIAO'\n{}FIM_DO_GUARDIAO\n\
             chmod 700 {script} && {depois}",
            self.script_sh()
        )
    }

    /// O AppleScript que pede a senha (janela do próprio macOS) e roda `comando_mac`.
    pub fn applescript_mac(&self, motivo: &str) -> String {
        format!(
            "do shell script {} with prompt {} with administrator privileges",
            aspas_applescript(&self.comando_mac()),
            aspas_applescript(motivo)
        )
    }
}

fn xml(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

// ------------------------------------------------------------------ Windows

const SCRIPT_PS: &str = r#"# Guardião do bloqueio de sites do estuda-ai. Gerado pelo app; roda como administrador.
# Modos: aplicar | vigiar | restaurar. Explicação em docs/modo-foco.md (bloqueio de sites).
param([string]$Modo)
$ErrorActionPreference = 'Stop'
$Hosts = @HOSTS@
$Estado = @ESTADO@
$Sinal = @SINAL@
$AppPid = @PID@
$Nome = @NOME@
$Ate = @ATE@
$Token = @TOKEN@
$Teste = @TESTE@
$Tarefa = @TAREFA@
$Inicio = @INICIO@
$Fim = @FIM@
$Dominios = @(@DOMINIOS@)

function Caminho($nome) { Join-Path $Estado $nome }

function Iguais($a, $b) {
  (Test-Path -LiteralPath $a) -and (Test-Path -LiteralPath $b) -and
    ((Get-FileHash -LiteralPath $a).Hash -eq (Get-FileHash -LiteralPath $b).Hash)
}

function Limpar-Cache {
  if (-not $Teste) { ipconfig /flushdns | Out-Null }
}

function Restaurar {
  if (Iguais $Hosts (Caminho 'hosts.bloqueado')) {
    # Ninguém mexeu: volta a cópia original, byte a byte
    [IO.File]::WriteAllBytes($Hosts, [IO.File]::ReadAllBytes((Caminho 'hosts.antes')))
  } elseif (Test-Path -LiteralPath $Hosts) {
    $texto = [IO.File]::ReadAllText($Hosts)
    if ($texto.Contains($Inicio)) {
      # Alguém editou o hosts durante a sessão: tira só o bloco, preserva o resto
      $padrao = '(\r?\n)?' + [regex]::Escape($Inicio) + '[\s\S]*?' + [regex]::Escape($Fim) + '(\r?\n)?'
      [IO.File]::WriteAllText($Hosts, [regex]::Replace($texto, $padrao, ''))
    }
  }
  foreach ($n in 'hosts.antes', 'hosts.bloqueado', 'vivo', 'dono') {
    Remove-Item -LiteralPath (Caminho $n) -Force -ErrorAction SilentlyContinue
  }
  Limpar-Cache
  if (-not $Teste) {
    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
  }
}

function Aplicar {
  New-Item -ItemType Directory -Force -Path $Estado | Out-Null
  Restaurar # sobra de uma sessão anterior que travou
  $original = [IO.File]::ReadAllBytes($Hosts)
  [IO.File]::WriteAllBytes((Caminho 'hosts.antes'), $original)
  $linhas = @($Inicio)
  foreach ($d in $Dominios) { $linhas += "0.0.0.0 $d", "0.0.0.0 www.$d", ":: $d", ":: www.$d" }
  $linhas += $Fim
  $bloco = [Text.Encoding]::ASCII.GetBytes("`r`n" + ($linhas -join "`r`n") + "`r`n")
  [byte[]]$novo = $original + $bloco
  [IO.File]::WriteAllBytes((Caminho 'hosts.bloqueado'), $novo)
  [IO.File]::WriteAllText((Caminho 'dono'), $Token)
  if (-not $Teste) {
    $script = Caminho 'guardiao.ps1'
    $acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
      -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" restaurar"
    Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger (New-ScheduledTaskTrigger -AtStartup) `
      -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
  }
  try { [IO.File]::WriteAllBytes($Hosts, $novo) } catch { Restaurar; throw }
  [IO.File]::WriteAllText((Caminho 'vivo'), [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
  Limpar-Cache
}

function Vigiar {
  while ($true) {
    $dono = $null
    if (Test-Path -LiteralPath (Caminho 'dono')) { $dono = [IO.File]::ReadAllText((Caminho 'dono')) }
    # Outro guardião assumiu (sessão nova): sai sem mexer no bloqueio dele
    if ($dono -ne $Token) { return }
    if (Test-Path -LiteralPath $Sinal) { break }
    $p = Get-Process -Id $AppPid -ErrorAction SilentlyContinue
    if (-not $p -or $p.ProcessName -notlike "*$Nome*") { break }
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $Ate) { break }
    [IO.File]::WriteAllText((Caminho 'vivo'), [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
    Start-Sleep -Seconds 2
  }
  Restaurar
}

switch ($Modo) {
  'aplicar' { Aplicar }
  'vigiar' { Vigiar }
  'restaurar' { Restaurar }
  default { Write-Error 'uso: guardiao.ps1 aplicar|vigiar|restaurar'; exit 2 }
}
"#;

impl Guardiao {
    /// O script do guardião para o PowerShell (Windows).
    pub fn script_ps(&self) -> String {
        let dominios: Vec<String> = self.dominios.iter().map(|d| aspas_ps(d)).collect();
        SCRIPT_PS
            .replace("@HOSTS@", &aspas_ps(&self.hosts))
            .replace("@ESTADO@", &aspas_ps(&self.estado))
            .replace("@SINAL@", &aspas_ps(&self.sinal))
            .replace("@PID@", &self.pid.to_string())
            .replace("@NOME@", &aspas_ps(&self.nome_processo))
            .replace("@ATE@", &self.ate.to_string())
            .replace("@TOKEN@", &aspas_ps(&self.token))
            .replace("@TESTE@", if self.teste { "$true" } else { "$false" })
            .replace("@TAREFA@", &aspas_ps(TAREFA_WINDOWS))
            .replace("@INICIO@", &aspas_ps(MARCA_INICIO))
            .replace("@FIM@", &aspas_ps(MARCA_FIM))
            .replace("@DOMINIOS@", &dominios.join(", "))
    }

    /// O que roda como administrador no Windows (via `-EncodedCommand`): cria a pasta
    /// do sistema com permissão só de leitura para usuários comuns (SIDs fixos: SYSTEM,
    /// Administradores, Usuários; nomes mudam com o idioma do Windows), grava o script,
    /// aplica e fica vigiando neste mesmo processo (que já é administrador).
    pub fn comando_windows(&self) -> String {
        self.gravar_script_windows(
            "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script aplicar\n\
             if ($LASTEXITCODE -ne 0) { exit 1 }\n\
             & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script vigiar\n",
        )
    }

    /// Só restaurar (o botão "desbloquear agora", quando o guardião não respondeu).
    pub fn comando_windows_restaurar(&self) -> String {
        self.gravar_script_windows(
            "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script restaurar\n",
        )
    }

    fn gravar_script_windows(&self, depois: &str) -> String {
        let estado = aspas_ps(&self.estado);
        format!(
            "$ErrorActionPreference = 'Stop'\n\
             $estado = {estado}\n\
             New-Item -ItemType Directory -Force -Path $estado | Out-Null\n\
             icacls $estado /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null\n\
             $script = Join-Path $estado 'guardiao.ps1'\n\
             $conteudo = @'\n{}\n'@\n\
             [IO.File]::WriteAllText($script, $conteudo, (New-Object Text.UTF8Encoding $true))\n\
             {depois}",
            self.script_ps()
        )
    }
}

// ------------------------------------------------------------------ programas

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sistema {
    Macos,
    Windows,
}

/// Nunca fechados, mesmo se estiverem na lista: o sistema (fechar o Finder ou o
/// explorer.exe deixa a tela sem barra de tarefas/Dock), o próprio app e o Spotify, que
/// toca a música da sessão. Comparação sem diferença de maiúsculas.
const PROTEGIDOS_MAC: &[&str] = &[
    "finder",
    "dock",
    "loginwindow",
    "windowserver",
    "systemuiserver",
    "launchd",
    "kernel_task",
    "controlcenter",
    "spotify",
    "estuda-ai",
    "terminal",
    "activity monitor",
];
const PROTEGIDOS_WINDOWS: &[&str] = &[
    "explorer.exe",
    "dwm.exe",
    "csrss.exe",
    "winlogon.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "smss.exe",
    "system",
    "taskmgr.exe",
    "spotify.exe",
    "estuda-ai.exe",
    "conhost.exe",
    "sihost.exe",
    "ctfmon.exe",
];

pub fn protegido(nome_processo: &str, sistema: Sistema) -> bool {
    let n = nome_processo.to_lowercase();
    let lista = match sistema {
        Sistema::Macos => PROTEGIDOS_MAC,
        Sistema::Windows => PROTEGIDOS_WINDOWS,
    };
    lista.contains(&n.as_str())
}

/// Qual entrada da lista manda fechar este processo (`None` = deixa).
///
/// - Windows: o nome do processo, com ou sem ".exe" ("Discord" fecha Discord.exe).
/// - macOS: o nome do executável, OU qualquer processo de dentro do pacote
///   "<entrada>.app": um app do Mac tem vários processos ("Discord Helper (Renderer)"),
///   e fechar só o principal deixaria os ajudantes vivos.
pub fn entrada_que_fecha<'a>(
    nome_processo: &str,
    caminho_executavel: Option<&str>,
    lista: &'a [String],
    sistema: Sistema,
) -> Option<&'a str> {
    if protegido(nome_processo, sistema) {
        return None;
    }
    let nome = nome_processo.to_lowercase();
    let caminho = caminho_executavel.map(str::to_lowercase);
    lista.iter().map(|e| e.trim()).find(|e| {
        let e_min = e.to_lowercase();
        if e_min.is_empty() || protegido(&e_min, sistema) {
            return false;
        }
        match sistema {
            Sistema::Windows => {
                nome == e_min || nome == format!("{}.exe", e_min.trim_end_matches(".exe"))
            }
            Sistema::Macos => {
                nome == e_min
                    || caminho.as_deref().is_some_and(|c| {
                        c.contains(&format!("/{}.app/", e_min.trim_end_matches(".app")))
                    })
            }
        }
    })
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn dominios_validos_e_invalidos() {
        for ok in ["youtube.com", "m.facebook.com", "x-y.co", "a1.b2.io"] {
            assert!(dominio_valido(ok), "{ok}");
        }
        for ruim in [
            "",
            "localhost",
            "YouTube.com",
            "a.com\n1.2.3.4 banco.com",
            "a .com",
            "-a.com",
            "a-.com",
            "a..com",
            "192.168.0.1",
            "a.c0m",
            "a.c",
            "a.com.",
        ] {
            assert!(!dominio_valido(ruim), "{ruim:?}");
        }
        assert!(!dominio_valido(&format!("{}.com", "a".repeat(64))));
    }

    #[test]
    fn lista_para_bloquear_descarta_invalidos_e_repetidos() {
        let lista: Vec<String> = ["youtube.com", "x y.com", "youtube.com", "reddit.com"]
            .map(String::from)
            .to_vec();
        assert_eq!(
            dominios_para_bloquear(&lista),
            ["youtube.com", "reddit.com"]
        );
        let muitos: Vec<String> = (0..300).map(|i| format!("s{i}.com")).collect();
        assert_eq!(dominios_para_bloquear(&muitos).len(), MAX_SITES);
    }

    #[test]
    fn bloco_tem_ipv4_ipv6_e_www() {
        assert_eq!(
            linhas_do_bloco(&["x.com".to_owned()]),
            [
                "0.0.0.0 x.com",
                "0.0.0.0 www.x.com",
                ":: x.com",
                ":: www.x.com"
            ]
        );
    }

    #[test]
    fn aspas() {
        assert_eq!(aspas_sh("a'b c"), r"'a'\''b c'");
        assert_eq!(aspas_applescript("a\"b\\c\nd"), r#""a\"b\\c\nd""#);
        assert_eq!(aspas_ps("a'b"), "'a''b'");
        // "ab" em UTF-16LE = 61 00 62 00
        assert_eq!(
            comando_codificado_ps("ab"),
            STANDARD.encode([0x61, 0, 0x62, 0])
        );
    }

    #[test]
    fn programas_por_sistema() {
        let lista: Vec<String> = ["Discord", "steam.exe", "Finder"]
            .map(String::from)
            .to_vec();
        let w = Sistema::Windows;
        assert_eq!(
            entrada_que_fecha("Discord.exe", None, &lista, w),
            Some("Discord")
        );
        assert_eq!(
            entrada_que_fecha("STEAM.EXE", None, &lista, w),
            Some("steam.exe")
        );
        assert_eq!(entrada_que_fecha("DiscordPTB.exe", None, &lista, w), None);
        let m = Sistema::Macos;
        assert_eq!(
            entrada_que_fecha("Discord", None, &lista, m),
            Some("Discord")
        );
        // Ajudante de dentro do pacote também fecha
        let ajudante = "/Applications/Discord.app/Contents/Frameworks/Discord Helper.app/Contents/MacOS/Discord Helper";
        assert_eq!(
            entrada_que_fecha("Discord Helper", Some(ajudante), &lista, m),
            Some("Discord")
        );
        assert_eq!(
            entrada_que_fecha(
                "Safari",
                Some("/Applications/Safari.app/Contents/MacOS/Safari"),
                &lista,
                m
            ),
            None
        );
    }

    #[test]
    fn protegidos_nunca_fecham_mesmo_na_lista() {
        let lista: Vec<String> = ["Finder", "explorer", "Spotify", "estuda-ai"]
            .map(String::from)
            .to_vec();
        assert_eq!(
            entrada_que_fecha("Finder", None, &lista, Sistema::Macos),
            None
        );
        assert_eq!(
            entrada_que_fecha("Spotify", None, &lista, Sistema::Macos),
            None
        );
        assert_eq!(
            entrada_que_fecha("explorer.exe", None, &lista, Sistema::Windows),
            None
        );
        assert_eq!(
            entrada_que_fecha("estuda-ai.exe", None, &lista, Sistema::Windows),
            None
        );
    }

    /// Prazo "nunca" dos testes (ano 2096). u64::MAX estouraria a aritmética do `sh`.
    const LONGE: u64 = 4_000_000_000;

    fn guardiao(dir: &std::path::Path) -> Guardiao {
        Guardiao {
            hosts: dir.join("hosts").display().to_string(),
            estado: dir.join("estado com espaço").display().to_string(),
            sinal: dir.join("liberar").display().to_string(),
            pid: std::process::id(),
            nome_processo: "x".into(),
            ate: 0,
            token: "t1".into(),
            dominios: vec!["youtube.com".into(), "reddit.com".into()],
            teste: true,
        }
    }

    #[test]
    fn script_cita_tudo_o_que_vem_de_fora() {
        let mut g = guardiao(std::path::Path::new("/tmp/a'b"));
        g.dominios = vec!["x.com".into()];
        let s = g.script_sh();
        assert!(s.contains(r"HOSTS='/tmp/a'\''b/hosts'"));
        assert!(!s.contains("@HOSTS@") && !s.contains("@DOMINIOS@"));
        let ps = g.script_ps();
        assert!(ps.contains("$Hosts = '/tmp/a''b/hosts'"));
        for marcador in [
            "@HOSTS@",
            "@ESTADO@",
            "@SINAL@",
            "@PID@",
            "@NOME@",
            "@ATE@",
            "@TOKEN@",
            "@TESTE@",
            "@DOMINIOS@",
        ] {
            assert!(
                !s.contains(marcador) && !ps.contains(marcador),
                "sobrou {marcador}"
            );
        }
    }

    // ------------------------------------------------- executando de verdade (sh)

    #[cfg(unix)]
    mod sh {
        use super::*;
        use std::fs;
        use std::process::{Command, Stdio};
        use std::time::{Duration, Instant};

        fn rodar(script: &std::path::Path, modo: &str) {
            let ok = Command::new("sh").arg(script).arg(modo).status().unwrap();
            assert!(ok.success(), "{modo} falhou");
        }

        fn preparar(original: &[u8]) -> (tempfile::TempDir, Guardiao, std::path::PathBuf) {
            let dir = tempfile::tempdir().unwrap();
            let g = guardiao(dir.path());
            fs::write(&g.hosts, original).unwrap();
            let script = dir.path().join("guardiao.sh");
            fs::write(&script, g.script_sh()).unwrap();
            (dir, g, script)
        }

        const ORIGINAIS: &[&[u8]] = &[
            b"127.0.0.1 localhost\n255.255.255.255 broadcasthost\n::1 localhost\n",
            b"127.0.0.1 localhost", // sem quebra de linha no fim
            b"127.0.0.1 localhost\r\n::1 localhost\r\n", // quebras do Windows
            b"",                    // vazio
        ];

        #[test]
        fn aplicar_e_restaurar_volta_byte_a_byte() {
            for original in ORIGINAIS {
                let (_dir, g, script) = preparar(original);
                rodar(&script, "aplicar");
                let bloqueado = fs::read_to_string(&g.hosts).unwrap();
                assert!(bloqueado.contains("0.0.0.0 www.youtube.com\n:: youtube.com\n:: www.youtube.com\n0.0.0.0 reddit.com"));
                assert!(bloqueado.contains(MARCA_INICIO) && bloqueado.contains(MARCA_FIM));
                rodar(&script, "restaurar");
                assert_eq!(fs::read(&g.hosts).unwrap(), *original);
                // De novo: sem bloqueio, restaurar não muda nada
                rodar(&script, "restaurar");
                assert_eq!(fs::read(&g.hosts).unwrap(), *original);
            }
        }

        #[test]
        fn travou_no_meio_e_a_restauracao_do_boot_conserta() {
            // "Aplicar" de novo depois de um travamento (sessão nova sem ter liberado):
            // não empilha dois blocos, e a restauração ainda volta o ORIGINAL
            let original = ORIGINAIS[0];
            let (_dir, g, script) = preparar(original);
            rodar(&script, "aplicar");
            rodar(&script, "aplicar");
            assert_eq!(
                fs::read_to_string(&g.hosts)
                    .unwrap()
                    .matches(MARCA_INICIO)
                    .count(),
                1
            );
            rodar(&script, "restaurar"); // o que o LaunchDaemon roda no boot
            assert_eq!(fs::read(&g.hosts).unwrap(), original);
        }

        #[test]
        fn editado_durante_a_sessao_tira_so_o_bloco() {
            let (_dir, g, script) = preparar(ORIGINAIS[0]);
            rodar(&script, "aplicar");
            let mut texto = fs::read_to_string(&g.hosts).unwrap();
            texto.push_str("10.0.0.5 impressora\n");
            fs::write(&g.hosts, &texto).unwrap();
            rodar(&script, "restaurar");
            let depois = fs::read_to_string(&g.hosts).unwrap();
            assert!(!depois.contains("youtube") && !depois.contains(MARCA_INICIO));
            assert!(
                depois.starts_with("127.0.0.1 localhost\n")
                    && depois.contains("10.0.0.5 impressora")
            );
        }

        fn esperar_restaurado(g: &Guardiao, original: &[u8]) {
            let fim = Instant::now() + Duration::from_secs(15);
            while fs::read(&g.hosts).unwrap() != original {
                assert!(Instant::now() < fim, "o guardião não restaurou");
                std::thread::sleep(Duration::from_millis(200));
            }
        }

        #[test]
        fn vigia_libera_quando_o_app_morre() {
            // O "app" é um sleep: quando ele termina (o app travou/fechou), o vigia libera
            let mut app = Command::new("sleep").arg("3").spawn().unwrap();
            let dir = tempfile::tempdir().unwrap();
            let mut g = guardiao(dir.path());
            g.pid = app.id();
            g.nome_processo = "sleep".into();
            g.ate = LONGE;
            let original = ORIGINAIS[0];
            fs::write(&g.hosts, original).unwrap();
            let script = dir.path().join("guardiao.sh");
            fs::write(&script, g.script_sh()).unwrap();
            rodar(&script, "aplicar");
            let mut vigia = Command::new("sh")
                .arg(&script)
                .arg("vigiar")
                .stdout(Stdio::null())
                .spawn()
                .unwrap();
            std::thread::sleep(Duration::from_millis(500));
            assert_ne!(
                fs::read(&g.hosts).unwrap(),
                original,
                "ainda bloqueado com o app vivo"
            );
            app.wait().unwrap();
            esperar_restaurado(&g, original);
            vigia.wait().unwrap();
        }

        #[test]
        fn vigia_libera_com_o_sinal_e_no_prazo() {
            let original = ORIGINAIS[0];
            // Sinal
            let (_dir, mut g, script) = preparar(original);
            g.nome_processo = String::new(); // qualquer nome: o processo é o do teste
            g.ate = LONGE;
            fs::write(&script, g.script_sh()).unwrap();
            rodar(&script, "aplicar");
            let mut vigia = Command::new("sh")
                .arg(&script)
                .arg("vigiar")
                .spawn()
                .unwrap();
            fs::write(&g.sinal, b"").unwrap();
            esperar_restaurado(&g, original);
            vigia.wait().unwrap();
            // Prazo já vencido
            let (_dir2, mut g2, script2) = preparar(original);
            g2.nome_processo = String::new();
            g2.ate = 1;
            fs::write(&script2, g2.script_sh()).unwrap();
            rodar(&script2, "aplicar");
            rodar(&script2, "vigiar");
            assert_eq!(fs::read(&g2.hosts).unwrap(), original);
        }

        #[test]
        fn vigia_antigo_nao_desfaz_o_bloqueio_da_sessao_nova() {
            let original = ORIGINAIS[0];
            let (_dir, mut antigo, script) = preparar(original);
            antigo.nome_processo = String::new();
            antigo.ate = LONGE;
            fs::write(&script, antigo.script_sh()).unwrap();
            rodar(&script, "aplicar");
            // Sessão nova: outro token assume a mesma pasta de estado
            let mut novo = antigo.clone();
            novo.token = "t2".into();
            let script_novo = script.with_file_name("novo.sh");
            fs::write(&script_novo, novo.script_sh()).unwrap();
            rodar(&script_novo, "aplicar");
            // O vigia antigo acorda, vê que não é mais o dono e sai SEM restaurar
            rodar(&script, "vigiar");
            assert!(
                fs::read_to_string(&antigo.hosts)
                    .unwrap()
                    .contains(MARCA_INICIO)
            );
            rodar(&script_novo, "restaurar");
            assert_eq!(fs::read(&antigo.hosts).unwrap(), original);
        }
    }

    /// O caminho inteiro do Mac, sem só a senha: AppleScript -> `do shell script` ->
    /// heredoc que grava o script -> aplicar -> vigia em segundo plano -> sinal.
    #[cfg(target_os = "macos")]
    #[test]
    fn caminho_do_applescript_no_mac() {
        use std::time::{Duration, Instant};
        let dir = tempfile::tempdir().unwrap();
        let mut g = guardiao(dir.path());
        g.nome_processo = String::new();
        g.ate = LONGE;
        let original = b"127.0.0.1 localhost\n";
        std::fs::write(&g.hosts, original).unwrap();
        let fonte = format!("do shell script {}", aspas_applescript(&g.comando_mac()));
        let saida = std::process::Command::new("osascript")
            .arg("-e")
            .arg(fonte)
            .output()
            .unwrap();
        assert!(
            saida.status.success(),
            "{}",
            String::from_utf8_lossy(&saida.stderr)
        );
        assert!(
            std::fs::read_to_string(&g.hosts)
                .unwrap()
                .contains("0.0.0.0 youtube.com")
        );
        std::fs::write(&g.sinal, b"").unwrap();
        let fim = Instant::now() + Duration::from_secs(15);
        while std::fs::read(&g.hosts).unwrap() != original {
            assert!(Instant::now() < fim, "o vigia em segundo plano não liberou");
            std::thread::sleep(Duration::from_millis(200));
        }
    }

    // ------------------------------------------ executando de verdade (PowerShell)

    #[cfg(windows)]
    mod powershell {
        use super::*;
        use std::fs;
        use std::process::Command;
        use std::time::{Duration, Instant};

        fn ps(script: &std::path::Path) -> Command {
            let mut c = Command::new("powershell.exe");
            c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
                .arg(script);
            c
        }

        fn rodar(script: &std::path::Path, modo: &str) {
            let saida = ps(script).arg(modo).output().unwrap();
            assert!(
                saida.status.success(),
                "{modo}: {}",
                String::from_utf8_lossy(&saida.stderr)
            );
        }

        fn preparar(
            original: &[u8],
            g: impl FnOnce(&mut Guardiao),
        ) -> (tempfile::TempDir, Guardiao, std::path::PathBuf) {
            let dir = tempfile::tempdir().unwrap();
            let mut guarda = guardiao(dir.path());
            g(&mut guarda);
            fs::write(&guarda.hosts, original).unwrap();
            let script = dir.path().join("guardiao.ps1");
            // Com BOM: o PowerShell 5 lê .ps1 sem BOM como ANSI (o "ç" do caminho viraria lixo)
            let mut bytes = vec![0xEF, 0xBB, 0xBF];
            bytes.extend_from_slice(guarda.script_ps().as_bytes());
            fs::write(&script, bytes).unwrap();
            (dir, guarda, script)
        }

        const ORIGINAIS: &[&[u8]] = &[
            b"127.0.0.1 localhost\r\n::1 localhost\r\n",
            b"# sem quebra no fim\r\n127.0.0.1 localhost",
            b"127.0.0.1 localhost\n", // só LF
        ];

        #[test]
        fn aplicar_e_restaurar_volta_byte_a_byte() {
            for original in ORIGINAIS {
                let (_dir, g, script) = preparar(original, |_| {});
                rodar(&script, "aplicar");
                let bloqueado = fs::read_to_string(&g.hosts).unwrap();
                assert!(bloqueado.contains("0.0.0.0 www.youtube.com\r\n:: youtube.com\r\n:: www.youtube.com\r\n0.0.0.0 reddit.com"));
                rodar(&script, "aplicar"); // de novo, como depois de um travamento
                assert_eq!(
                    fs::read_to_string(&g.hosts)
                        .unwrap()
                        .matches(MARCA_INICIO)
                        .count(),
                    1
                );
                rodar(&script, "restaurar");
                assert_eq!(fs::read(&g.hosts).unwrap(), *original);
                rodar(&script, "restaurar");
                assert_eq!(fs::read(&g.hosts).unwrap(), *original);
            }
        }

        #[test]
        fn editado_durante_a_sessao_tira_so_o_bloco() {
            let (_dir, g, script) = preparar(ORIGINAIS[0], |_| {});
            rodar(&script, "aplicar");
            let mut texto = fs::read_to_string(&g.hosts).unwrap();
            texto.push_str("10.0.0.5 impressora\r\n");
            fs::write(&g.hosts, &texto).unwrap();
            rodar(&script, "restaurar");
            let depois = fs::read_to_string(&g.hosts).unwrap();
            assert!(!depois.contains("youtube") && depois.contains("10.0.0.5 impressora"));
        }

        #[test]
        fn vigia_libera_quando_o_app_morre_e_com_o_sinal() {
            // O "app" é um ping de ~3 s: quando ele termina, o vigia libera
            let mut app = Command::new("ping")
                .args(["-n", "4", "127.0.0.1"])
                .spawn()
                .unwrap();
            let original = ORIGINAIS[0];
            let (_dir, g, script) = preparar(original, |g| {
                g.pid = 0; // trocado abaixo
                g.ate = LONGE;
            });
            let mut g = g;
            g.pid = app.id();
            g.nome_processo = "PING".into();
            let mut bytes = vec![0xEF, 0xBB, 0xBF];
            bytes.extend_from_slice(g.script_ps().as_bytes());
            fs::write(&script, bytes).unwrap();
            rodar(&script, "aplicar");
            let mut vigia = ps(&script).arg("vigiar").spawn().unwrap();
            app.wait().unwrap();
            let fim = Instant::now() + Duration::from_secs(20);
            while fs::read(&g.hosts).unwrap() != original {
                assert!(Instant::now() < fim, "o guardião não restaurou");
                std::thread::sleep(Duration::from_millis(250));
            }
            vigia.wait().unwrap();
        }
    }
}
