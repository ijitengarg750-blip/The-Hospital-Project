from datetime import datetime, timedelta, timezone
import hmac

import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

bearer = HTTPBearer(auto_error=False)


def authenticate(username: str, password: str) -> bool:
    settings = get_settings()
    return hmac.compare_digest(username, settings.doctor_username) and hmac.compare_digest(
        password, settings.doctor_password
    )


def create_token(username: str) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": username, "exp": expires}, settings.jwt_secret, algorithm="HS256")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if not credentials:
        raise HTTPException(401, "Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, get_settings().jwt_secret, algorithms=["HS256"])
        return str(payload["sub"])
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(401, "Invalid or expired token")


def require_collector(x_collector_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_collector_key, get_settings().collector_api_key):
        raise HTTPException(401, "Invalid collector key")

