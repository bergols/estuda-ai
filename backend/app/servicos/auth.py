"""Senhas (argon2id) e tokens (JWT).

POR QUE ARGON2ID (e não bcrypt):
- Um hash de senha precisa ser LENTO e CARO de propósito: quem roubar o banco vai
  testar bilhões de senhas por segundo numa GPU. bcrypt é caro em CPU; argon2id
  também é caro em MEMÓRIA (aqui 64 MiB por tentativa), e memória é o que as GPUs e
  os chips dedicados têm pouco por núcleo: atacar em paralelo fica muito mais caro.
- Venceu a Password Hashing Competition (2015) e é a 1a recomendação do OWASP.
- bcrypt ignora tudo depois do 72o byte da senha (frases longas viram a mesma
  senha) e tem só um parâmetro de custo.
- O hash guarda os próprios parâmetros e o "sal" aleatório:
  $argon2id$v=19$m=65536,t=3,p=4$<sal>$<hash>. O mesmo texto gera hashes diferentes
  a cada vez, e dá para aumentar o custo no futuro (check_needs_rehash).

JWT (JSON Web Token): {"sub": id, "ver": versao_token, "iat", "exp"} assinado com
HMAC-SHA256 e o segredo do servidor. Não é criptografado (qualquer um lê o
conteúdo em base64); é ASSINADO (ninguém altera sem o segredo). O servidor não
guarda sessão: valida a assinatura e a validade a cada requisição.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Usuario

ALGORITMO = "HS256"
SENHA_MINIMA = 12
_hasher = PasswordHasher()  # argon2id, m=64 MiB, t=3, p=4 (perfil "low memory" da RFC 9106)
# Hash de uma senha qualquer, para gastar o mesmo tempo quando o e-mail não existe.
_HASH_FALSO = _hasher.hash("senha-que-nao-existe-de-ninguem")


class ErroAuth(Exception):
    pass


def gerar_hash(senha: str) -> str:
    if len(senha) < SENHA_MINIMA:
        raise ValueError(f"a senha precisa ter pelo menos {SENHA_MINIMA} caracteres")
    return _hasher.hash(senha)


def consulta_por_email(email: str) -> Select[tuple[Usuario]]:
    """lower(email) = lower(:email): a MESMA expressão do índice único
    uq_usuarios_email_lower, então a busca usa o índice.

    Não use ILIKE para "ignorar maiúsculas": em LIKE/ILIKE, % e _ são CURINGAS. Com
    ILIKE, o e-mail "%" casava com qualquer conta (bastava a senha certa) e cada
    variação ("%", "%%", "d%") virava uma chave nova no limite de falhas por e-mail.
    É uma injection sem SQL nenhum: o valor vai como bind parameter, mas o OPERADOR
    interpreta o conteúdo. Além disso, ILIKE não usa o índice de lower(email).
    """
    return select(Usuario).where(func.lower(Usuario.email) == email.strip().lower())


def autenticar(session: Session, email: str, senha: str) -> Usuario:
    """Devolve o usuário se e-mail e senha conferem; senão ErroAuth (mesma mensagem
    para "e-mail não existe" e "senha errada", para não revelar quais e-mails existem)."""
    usuario = session.scalar(consulta_por_email(email))
    if usuario is None or usuario.senha_hash is None:
        # Verifica contra um hash falso para o tempo de resposta ser o mesmo: sem isso,
        # "e-mail inexistente" responderia em 1 ms e "senha errada" em 40 ms, e o tempo
        # revelaria quais e-mails têm conta (timing attack).
        _verificar(_HASH_FALSO, senha)
        raise ErroAuth("e-mail ou senha incorretos")
    if not _verificar(usuario.senha_hash, senha):
        raise ErroAuth("e-mail ou senha incorretos")
    if _hasher.check_needs_rehash(usuario.senha_hash):
        # Os parâmetros de custo mudaram desde que o hash foi feito: refaz agora, que
        # temos a senha em mãos (é o único momento em que dá para fazer isso).
        usuario.senha_hash = _hasher.hash(senha)
        session.commit()
    return usuario


def _verificar(senha_hash: str, senha: str) -> bool:
    try:
        return _hasher.verify(senha_hash, senha)
    except (VerificationError, InvalidHashError):
        return False


@dataclass
class Token:
    valor: str
    expira_em: datetime


def emitir_token(usuario: Usuario, agora: datetime | None = None) -> Token:
    settings = get_settings()
    agora = agora or datetime.now(UTC)
    expira = agora + timedelta(days=settings.jwt_dias)
    conteudo = {"sub": str(usuario.id), "ver": usuario.versao_token, "iat": agora, "exp": expira}
    valor = jwt.encode(conteudo, settings.jwt_secret.get_secret_value(), algorithm=ALGORITMO)
    return Token(valor, expira)


def ler_token(valor: str) -> tuple[int, int]:
    """(usuario_id, versao_token) de um token válido; senão ErroAuth.

    algorithms=[ALGORITMO] é obrigatório: sem ele, um token com "alg": "none" (sem
    assinatura) ou assinado com outro algoritmo poderia ser aceito.
    """
    try:
        conteudo = jwt.decode(
            valor, get_settings().jwt_secret.get_secret_value(), algorithms=[ALGORITMO],
            options={"require": ["sub", "ver", "exp", "iat"]},
        )
        return int(conteudo["sub"]), int(conteudo["ver"])
    except (jwt.InvalidTokenError, ValueError) as erro:
        raise ErroAuth("token inválido ou expirado") from erro


def revogar_tokens(session: Session, usuario_id: int) -> None:
    """Invalida todos os tokens do usuário (versao_token + 1)."""
    session.execute(
        update(Usuario).where(Usuario.id == usuario_id).values(versao_token=Usuario.versao_token + 1)
    )
    session.commit()
