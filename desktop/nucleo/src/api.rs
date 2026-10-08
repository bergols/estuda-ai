//! Para onde o app desktop manda as chamadas da API, e quais caminhos aceita.
//!
//! O frontend (a página dentro da janela) pede "chame disciplinas/5/flashcards"; quem
//! monta a URL e acrescenta o token é o lado Rust. Esta é a fronteira de confiança:
//! se a página fosse comprometida (XSS por um PDF malicioso, por exemplo), ela ainda
//! não conseguiria (1) mandar o token para outro servidor nem (2) chamar rotas fora da
//! lista. As mesmas regras do BFF da web (frontend/src/lib/bff.ts), do lado de cá.

use percent_encoding::percent_decode_str;
use url::Url;

/// Primeiro segmento permitido (igual a PERMITIDOS em frontend/src/lib/bff.ts).
const PERMITIDOS: &[&str] = &["disciplinas", "revisoes", "analytics", "gastos"];
/// Em /auth, só estes: o login tem comando próprio (entrar), que guarda o token no cofre.
const AUTH_PERMITIDOS: &[&str] = &["eu", "sair-de-todos"];

#[derive(Debug, PartialEq, Eq)]
pub enum ErroUrl {
    /// Endereço do servidor inválido ou inseguro (http fora do localhost).
    BaseInvalida(String),
    /// Caminho fora da allowlist ou com truque de navegação ("..", "//", "\").
    CaminhoRecusado,
}

/// Endereço do servidor (o BFF na Vercel) a partir da configuração.
///
/// HTTPS é obrigatório: o token viaja em todas as chamadas. A exceção é o próprio
/// computador (desenvolvimento com `next dev` em http://localhost:3000), onde não há
/// rede no meio para alguém escutar.
pub fn base_da_api(endereco: &str) -> Result<Url, ErroUrl> {
    let url = Url::parse(endereco).map_err(|e| ErroUrl::BaseInvalida(e.to_string()))?;
    let local = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    match url.scheme() {
        "https" => {}
        "http" if local => {}
        outro => {
            return Err(ErroUrl::BaseInvalida(format!(
                "use https (http só para localhost), não {outro}"
            )));
        }
    }
    if url.username() != "" || url.password().is_some() {
        return Err(ErroUrl::BaseInvalida(
            "o endereço não pode ter usuário/senha".into(),
        ));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(ErroUrl::BaseInvalida(
            "o endereço não pode ter ?busca nem #fragmento".into(),
        ));
    }
    Ok(url)
}

/// URL final de uma chamada: `<base>/api/<caminho>[?busca]`.
///
/// `caminho` vem da página, já codificado como numa URL ("disciplinas/5/busca?q=%C3%A9").
/// Cada segmento é decodificado, conferido e recodificado pelo `url` ao ser
/// acrescentado com `path_segments_mut`, que trata cada pedaço como UM segmento:
/// não existe "/" ou "//host" que escape dali. No fim, a origem (esquema + host +
/// porta) é conferida de novo, por garantia: é ela que decide quem recebe o token.
pub fn url_da_chamada(base: &Url, caminho: &str) -> Result<Url, ErroUrl> {
    let (caminho, busca) = match caminho.split_once('?') {
        Some((c, b)) => (c, Some(b)),
        None => (caminho, None),
    };
    let segmentos = segmentos_permitidos(caminho)?;

    let mut url = base.clone();
    {
        let mut partes = url
            .path_segments_mut()
            .map_err(|_| ErroUrl::CaminhoRecusado)?;
        partes.pop_if_empty().push("api");
        for segmento in &segmentos {
            partes.push(segmento);
        }
    }
    // A busca vai como veio (o navegador/openapi-fetch já a codificou); o `url` só
    // re-escapa o que for inválido. Não há como ela mudar o host: vem depois do "?".
    url.set_query(busca.filter(|b| !b.is_empty()));

    if url.origin() != base.origin() {
        return Err(ErroUrl::CaminhoRecusado);
    }
    Ok(url)
}

/// Segmentos já decodificados, se o caminho passar por todas as regras.
fn segmentos_permitidos(caminho: &str) -> Result<Vec<String>, ErroUrl> {
    if caminho.is_empty() || caminho.contains('\\') || caminho.contains('#') {
        return Err(ErroUrl::CaminhoRecusado);
    }
    let mut segmentos = Vec::new();
    for bruto in caminho.split('/') {
        let decodificado = percent_decode_str(bruto)
            .decode_utf8()
            .map_err(|_| ErroUrl::CaminhoRecusado)?;
        // "" (barra dupla ou inicial), "." e ".." recusados em QUALQUER posição,
        // inclusive codificados ("%2e%2e"): mesma lição do bff.test.ts.
        if decodificado.is_empty() || decodificado == "." || decodificado == ".." {
            return Err(ErroUrl::CaminhoRecusado);
        }
        // Um "/" codificado (%2F) dentro de um segmento é dado, não separador; o push
        // abaixo o recodifica. Mas "\" decodificado viraria separador em alguns servidores.
        if decodificado.contains('\\') {
            return Err(ErroUrl::CaminhoRecusado);
        }
        segmentos.push(decodificado.into_owned());
    }

    let primeiro = segmentos[0].as_str();
    let permitido = if primeiro == "auth" {
        segmentos.len() == 2 && AUTH_PERMITIDOS.contains(&segmentos[1].as_str())
    } else {
        PERMITIDOS.contains(&primeiro)
    };
    if permitido {
        Ok(segmentos)
    } else {
        Err(ErroUrl::CaminhoRecusado)
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    fn base() -> Url {
        base_da_api("https://estuda-ai.example.com").unwrap()
    }

    #[test]
    fn monta_a_url_com_api_e_busca() {
        let url =
            url_da_chamada(&base(), "disciplinas/5/busca?q=%C3%ADndice&modo=hibrida").unwrap();
        assert_eq!(
            url.as_str(),
            "https://estuda-ai.example.com/api/disciplinas/5/busca?q=%C3%ADndice&modo=hibrida"
        );
    }

    #[test]
    fn base_com_barra_final_ou_subpasta() {
        let b = base_da_api("https://exemplo.com/app/").unwrap();
        let url = url_da_chamada(&b, "revisoes/hoje").unwrap();
        assert_eq!(url.as_str(), "https://exemplo.com/app/api/revisoes/hoje");
    }

    #[test]
    fn caminhos_da_allowlist() {
        for ok in [
            "disciplinas",
            "revisoes/hoje",
            "analytics/resumo",
            "gastos",
            "auth/eu",
            "auth/sair-de-todos",
        ] {
            assert!(url_da_chamada(&base(), ok).is_ok(), "{ok} devia passar");
        }
    }

    #[test]
    fn recusa_fora_da_allowlist() {
        for ruim in [
            "",
            "docs",
            "openapi.json",
            "auth/login",
            "auth",
            "auth/eu/x",
            "health",
        ] {
            assert_eq!(
                url_da_chamada(&base(), ruim),
                Err(ErroUrl::CaminhoRecusado),
                "{ruim}"
            );
        }
    }

    #[test]
    fn recusa_navegacao_por_pontos_mesmo_codificada() {
        for ruim in [
            "disciplinas/../docs",
            "disciplinas/./5",
            "disciplinas/%2e%2e/docs",
            "disciplinas/%2E%2E/docs",
            "disciplinas//5",
            "/disciplinas",
            "disciplinas/5/",
            "disciplinas\\..\\docs",
            "disciplinas/%5c..%5cdocs",
        ] {
            assert_eq!(
                url_da_chamada(&base(), ruim),
                Err(ErroUrl::CaminhoRecusado),
                "{ruim}"
            );
        }
    }

    #[test]
    fn o_token_nunca_sai_da_origem_configurada() {
        // Tentativas de trocar o host: viram segmentos inofensivos ou são recusadas.
        for tentativa in [
            "disciplinas/%2F%2Fevil.com",
            "disciplinas/@evil.com",
            "disciplinas/https:%2F%2Fevil.com",
            "disciplinas?x=1#@evil.com",
        ] {
            if let Ok(url) = url_da_chamada(&base(), tentativa) {
                assert_eq!(
                    url.host_str(),
                    Some("estuda-ai.example.com"),
                    "{tentativa} -> {url}"
                );
                assert!(
                    url.path().starts_with("/api/disciplinas"),
                    "{tentativa} -> {url}"
                );
            }
        }
    }

    #[test]
    fn barra_codificada_continua_dado() {
        let url = url_da_chamada(&base(), "disciplinas/a%2Fb").unwrap();
        assert_eq!(url.path(), "/api/disciplinas/a%2Fb");
    }

    #[test]
    fn base_exige_https_fora_do_localhost() {
        assert!(base_da_api("https://x.vercel.app").is_ok());
        assert!(base_da_api("http://localhost:3000").is_ok());
        assert!(base_da_api("http://127.0.0.1:3000").is_ok());
        assert!(matches!(
            base_da_api("http://x.vercel.app"),
            Err(ErroUrl::BaseInvalida(_))
        ));
        assert!(matches!(
            base_da_api("ftp://x.com"),
            Err(ErroUrl::BaseInvalida(_))
        ));
        assert!(matches!(
            base_da_api("https://u:s@x.com"),
            Err(ErroUrl::BaseInvalida(_))
        ));
        assert!(matches!(
            base_da_api("https://x.com/?a=1"),
            Err(ErroUrl::BaseInvalida(_))
        ));
        assert!(matches!(
            base_da_api("não é url"),
            Err(ErroUrl::BaseInvalida(_))
        ));
    }
}
