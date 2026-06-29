import hashlib
import secrets
import time
from dataclasses import dataclass

import jwt

ROLE_RANK: dict[str, int] = {"viewer": 0, "reviewer": 1, "admin": 2}


@dataclass
class Principal:
    tenant: str
    role: str
    sub: str  # the issuing key's kid


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 999)


def generate_api_key(tenant_id: str) -> str:
    return f"pk_{tenant_id}_{secrets.token_urlsafe(24)}"


def new_salt() -> str:
    return secrets.token_hex(16)


def new_kid() -> str:
    return "k_" + secrets.token_hex(6)


def hash_api_key(salt: str, raw_key: str) -> str:
    return hashlib.sha256((salt + raw_key).encode()).hexdigest()


def issue_jwt(principal: Principal, secret: str, ttl_seconds: int, now: float | None = None) -> str:
    iat = int(now if now is not None else time.time())
    payload = {
        "sub": principal.sub,
        "tenant": principal.tenant,
        "role": principal.role,
        "iat": iat,
        "exp": iat + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_jwt(token: str, secret: str) -> Principal:
    data = jwt.decode(token, secret, algorithms=["HS256"])
    return Principal(tenant=data["tenant"], role=data["role"], sub=data["sub"])
