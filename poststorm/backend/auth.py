import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from fastapi import Header, HTTPException, Request

from backend.ledger.models import ApiKey, Tenant

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
    data = jwt.decode(token, secret, algorithms=["HS256"],
                      options={"require": ["exp", "sub", "tenant", "role"]})
    return Principal(tenant=data["tenant"], role=data["role"], sub=data["sub"])


def create_tenant(session, tenant_id: str, name: str = "") -> None:
    if session.get(Tenant, tenant_id) is None:
        session.add(Tenant(id=tenant_id, name=name or tenant_id))
        session.flush()


def issue_key(session, tenant_id: str, role: str, raw_key: str | None = None) -> tuple[str, str]:
    raw = raw_key or generate_api_key(tenant_id)
    salt = new_salt()
    kid = new_kid()
    session.add(ApiKey(kid=kid, tenant_id=tenant_id, role=role,
                       key_hash=hash_api_key(salt, raw), salt=salt, active=True))
    session.flush()
    return kid, raw


def verify_api_key(session, raw_key: str) -> Principal | None:
    # Each key has its own salt, so we test against every active key.
    # Fine at demo scale (a handful of keys); a production store would index by hash.
    for k in session.query(ApiKey).filter_by(active=True).all():
        if hash_api_key(k.salt, raw_key) == k.key_hash:
            return Principal(tenant=k.tenant_id, role=k.role, sub=k.kid)
    return None


def revoke_key(session, kid: str) -> bool:
    k = session.get(ApiKey, kid)
    if k is None:
        return False
    k.active = False
    k.revoked_at = datetime.now(UTC)
    session.flush()
    return True


def demo_api_key(settings) -> str:
    digest = hashlib.sha256(f"demo-key|{settings.jwt_secret}".encode()).hexdigest()[:24]
    return f"pk_demo_{digest}"


def seed_tenants(session, settings) -> None:
    for spec in (s for s in settings.seed_tenants.split(",") if s.strip()):
        tid, _, role = spec.partition(":")
        tid = tid.strip()
        role = (role or "reviewer").strip()
        create_tenant(session, tid)
        if tid == "demo":
            raw = demo_api_key(settings)
            if verify_api_key(session, raw) is None:  # idempotent: only seed once
                issue_key(session, "demo", role, raw_key=raw)
        session.commit()


def require_principal(request: Request, authorization: str | None = Header(default=None)) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    from backend.config import get_settings  # local import avoids an import cycle at module load
    try:
        principal = verify_jwt(authorization[7:], get_settings().jwt_secret)
    except Exception as e:
        raise HTTPException(status_code=401, detail="invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from e
    request.state.principal = principal
    return principal


def require_role(minimum: str) -> Callable[..., Principal]:
    def dep(request: Request, authorization: str | None = Header(default=None)) -> Principal:
        principal = require_principal(request, authorization)
        if not role_at_least(principal.role, minimum):
            raise HTTPException(status_code=403, detail="insufficient role")
        return principal
    return dep
