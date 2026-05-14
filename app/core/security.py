"""Primitivas de segurança — hashing PBKDF2 e verificação constant-time.

Centraliza a forma de armazenar/verificar senhas em todo o sistema.
Formato do hash: "{salt_hex}${dk_hex}" — PBKDF2-HMAC-SHA256, 200_000 iterações.
"""

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000
_ALGO = "sha256"


def hash_password(password: str, salt: str = "") -> str:
    """Gera hash PBKDF2-HMAC-SHA256. Gera salt aleatório se não informado."""
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode(), salt.encode(), _ITERATIONS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifica senha contra hash armazenado em tempo constante."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, _ = stored_hash.split("$", 1)
        return hmac.compare_digest(hash_password(password, salt), stored_hash)
    except Exception:
        return False


def verify_password_setting(password: str, hashed_setting: str, plaintext_setting: str) -> bool:
    """Verifica senha de um usuário configurado via .env.

    Prefere o hash quando configurado; cai pra comparação plaintext (constant-time)
    apenas se o hash estiver vazio — caminho legado pra migração gradual.
    Retorna False se nenhuma configuração estiver presente.
    """
    if hashed_setting:
        return verify_password(password, hashed_setting)
    if plaintext_setting:
        return hmac.compare_digest(password.encode(), plaintext_setting.encode())
    return False
