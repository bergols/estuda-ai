"""Cria um usuário com senha, ou redefine a senha de um existente (comando de admin).

    docker compose exec backend python -m scripts.criar_usuario --email voce@exemplo.com --nome "Seu Nome"
    docker compose exec backend python -m scripts.criar_usuario --email voce@exemplo.com --redefinir-senha

O cadastro público está desabilitado: este é o único jeito de criar uma conta.
A senha é pedida no terminal (getpass, sem aparecer na tela) e NUNCA vai como
argumento da linha de comando: argumentos ficam no histórico do shell e aparecem
para outros usuários da máquina em "ps". Para uso não interativo (ex.: no deploy),
a senha pode vir da variável de ambiente ESTUDA_AI_SENHA.
Redefinir a senha também incrementa versao_token: todos os logins antigos caem.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Usuario
from app.servicos.auth import SENHA_MINIMA, gerar_hash


def pedir_senha() -> str:
    if senha := os.environ.get("ESTUDA_AI_SENHA"):
        return senha
    senha = getpass.getpass(f"Senha (mínimo {SENHA_MINIMA} caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    return senha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--nome")
    parser.add_argument("--fuso", default="America/Sao_Paulo")
    parser.add_argument("--redefinir-senha", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as session:
        usuario = session.scalar(select(Usuario).where(Usuario.email.ilike(args.email.strip())))
        if args.redefinir_senha:
            if usuario is None:
                sys.exit(f"Não existe usuário com o e-mail {args.email}.")
        elif usuario is not None:
            sys.exit(f"Já existe usuário com o e-mail {args.email}. Use --redefinir-senha.")
        elif not args.nome:
            sys.exit("Informe --nome para criar o usuário.")

        try:
            senha_hash = gerar_hash(pedir_senha())
        except ValueError as erro:
            sys.exit(str(erro))

        if usuario is None:
            usuario = Usuario(nome=args.nome, email=args.email.strip(),
                              fuso_horario=args.fuso, senha_hash=senha_hash)
            session.add(usuario)
            acao = "criado"
        else:
            usuario.senha_hash = senha_hash
            usuario.versao_token += 1  # derruba os logins antigos
            acao = "senha redefinida"
        session.commit()
        print(f"Usuário {usuario.id} ({usuario.email}): {acao}.")


if __name__ == "__main__":
    main()
