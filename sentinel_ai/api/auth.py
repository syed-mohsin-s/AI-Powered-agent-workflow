"""
Sentinel-AI JWT Authentication.

Handles JWT token generation, validation, and the /api/auth/token endpoint.
Supports a single configured admin user (for simplicity) with bcrypt-hashed passwords.
Falls back to dev-mode (any credentials accepted) when no password hash is configured.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from sentinel_ai.config import get_config
from sentinel_ai.models.schemas import TokenRequest, TokenResponse
from sentinel_ai.utils.logger import get_logger

logger = get_logger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def create_access_token(subject: str, scopes: list[str] | None = None) -> str:
    """Generate a signed JWT access token."""
    from jose import jwt as jose_jwt

    config = get_config()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=config.jwt.access_token_expire_minutes)

    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "scopes": scopes or ["admin"],
        "iss": "sentinel-ai",
    }
    return jose_jwt.encode(payload, config.jwt.secret_key, algorithm=config.jwt.algorithm)


def decode_token(token: str) -> dict:
    """Validate and decode a JWT token.  Returns the decoded payload."""
    from jose import JWTError, jwt as jose_jwt

    config = get_config()
    try:
        payload = jose_jwt.decode(
            token,
            config.jwt.secret_key,
            algorithms=[config.jwt.algorithm],
        )
        sub: str | None = payload.get("sub")
        if sub is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject",
            )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        from passlib.context import CryptContext

        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.verify(plain_password, hashed_password)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# FastAPI dependency — inject into protected routes
# ---------------------------------------------------------------------------


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    """FastAPI dependency that extracts the authenticated user from the Bearer token.

    When *no* JWT secret is configured (dev-mode) this dependency is effectively
    a no-op so that the system stays usable without tokens during local
    development.
    """
    config = get_config()

    # Dev-mode: if secret is the placeholder default, skip auth
    if config.jwt.secret_key in ("", "sentinel-ai-default-secret-change-me"):
        return {"sub": "dev-user", "scopes": ["admin"]}

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated — Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return decode_token(token)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/token", response_model=TokenResponse)
async def login(request: TokenRequest):
    """Authenticate and receive a JWT access token.

    In dev-mode (no password hash configured), any credentials are accepted.
    """
    config = get_config()

    # Validate credentials
    if config.jwt.default_password_hash:
        # Production mode — bcrypt check
        if request.username != config.jwt.default_username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        if not _verify_password(request.password, config.jwt.default_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
    else:
        # Dev-mode — accept any user
        logger.warning(
            "JWT dev-mode: accepting any credentials (no password hash configured)"
        )

    token = create_access_token(subject=request.username)
    logger.info(f"Token issued for user: {request.username}")

    return TokenResponse(
        access_token=token,
        expires_in_minutes=config.jwt.access_token_expire_minutes,
    )
