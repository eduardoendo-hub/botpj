#!/usr/bin/env python3
"""Gera hash PBKDF2 para preencher ADMIN_PASSWORD_HASH / CONSULTANT_PASSWORD_HASH no .env.

Uso (no servidor, pasta do projeto):
    python3 hash_password.py

A senha é lida sem eco no terminal (getpass). O hash impresso pode ser colado
direto no .env. A senha original NÃO é armazenada em lugar algum.
"""

import sys
from getpass import getpass

from app.core.security import hash_password


def main() -> int:
    print("Bot SDR PJ — Gerador de hash de senha (PBKDF2)")
    print("─" * 50)
    try:
        pwd = getpass("Senha: ")
    except (EOFError, KeyboardInterrupt):
        print("\nAbortado.", file=sys.stderr)
        return 1

    if len(pwd) < 8:
        print("Senha deve ter pelo menos 8 caracteres.", file=sys.stderr)
        return 1

    pwd2 = getpass("Confirme: ")
    if pwd != pwd2:
        print("Senhas não coincidem. Abortado.", file=sys.stderr)
        return 1

    h = hash_password(pwd)
    print()
    print("Hash gerado — cole no .env do servidor (use a chave apropriada):")
    print()
    print(f"ADMIN_PASSWORD_HASH={h}")
    print(f"# ou: CONSULTANT_PASSWORD_HASH={h}")
    print()
    print("Depois reinicie o serviço: sudo systemctl restart bot-sdr-pj")
    return 0


if __name__ == "__main__":
    sys.exit(main())
