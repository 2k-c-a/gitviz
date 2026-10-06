"""AI 입력 코드 암호화(AES-256-GCM). payload = nonce(12) || ciphertext+tag.

키 버전은 payload 밖(DB 컬럼)에 저장한다. 복호화 실패 원인은 외부에 구분해 알리지 않는다.
"""
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.ai_settings import get_ai_settings

NONCE_SIZE = 12


class InputCryptoError(Exception):
    """알 수 없는 키 버전·변조·키 불일치. 메시지에 민감 값을 담지 않는다."""


def _key(key_version: str) -> bytes:
    key = get_ai_settings().encryption_keys.get(key_version)
    if not key:
        raise InputCryptoError('알 수 없는 암호화 키 버전입니다.')
    return key


def encrypt_input(data: bytes, key_version: str, aad: bytes = b'') -> bytes:
    """aad(예: input_id)를 주면 다른 행으로 암호문을 옮겨 붙이는 것을 막는다."""
    nonce = secrets.token_bytes(NONCE_SIZE)
    return nonce + AESGCM(_key(key_version)).encrypt(nonce, data, aad or None)


def decrypt_input(payload: bytes, key_version: str, aad: bytes = b'') -> bytes:
    if len(payload) <= NONCE_SIZE:
        raise InputCryptoError('암호문이 올바르지 않습니다.')
    try:
        return AESGCM(_key(key_version)).decrypt(payload[:NONCE_SIZE], payload[NONCE_SIZE:], aad or None)
    except InvalidTag:
        raise InputCryptoError('암호문을 복호화할 수 없습니다.') from None


# ── 사용자 API key 암호화: 입력 코드 암호화 키와 분리된 키 묶음(AI_CREDENTIAL_ENCRYPTION_KEYS)을 쓴다 ──

def _credential_key(key_version: str) -> bytes:
    key = get_ai_settings().credential_keys.get(key_version)
    if not key:
        raise InputCryptoError('알 수 없는 API key 암호화 키 버전입니다.')
    return key


def encrypt_secret(data: bytes, key_version: str, aad: bytes) -> bytes:
    """aad(예: 'credential:<version id>')로 다른 행·다른 사용자에게 암호문을 옮겨 붙이는 것을 막는다."""
    nonce = secrets.token_bytes(NONCE_SIZE)
    return nonce + AESGCM(_credential_key(key_version)).encrypt(nonce, data, aad)


def decrypt_secret(payload: bytes, key_version: str, aad: bytes) -> bytes:
    if len(payload) <= NONCE_SIZE:
        raise InputCryptoError('암호문이 올바르지 않습니다.')
    try:
        return AESGCM(_credential_key(key_version)).decrypt(payload[:NONCE_SIZE], payload[NONCE_SIZE:], aad)
    except InvalidTag:
        raise InputCryptoError('암호문을 복호화할 수 없습니다.') from None

