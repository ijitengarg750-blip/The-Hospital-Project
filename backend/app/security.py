from datetime import datetime, timedelta, timezone
import hmac

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

bearer = HTTPBearer(auto_error=False)


class AuthenticatedUser(str):
    role: str

    def __new__(cls, username: str, role: str):
        value = str.__new__(cls, username)
        value.role = role
        return value


def authenticate(username: str, password: str) -> AuthenticatedUser | None:
    settings = get_settings()
    doctor = hmac.compare_digest(username, settings.doctor_username) and hmac.compare_digest(password, settings.doctor_password)
    admin = hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(password, settings.admin_password)
    if admin:
        return AuthenticatedUser(username, "admin")
    if doctor:
        return AuthenticatedUser(username, "doctor")
    return None


def create_token(user: AuthenticatedUser) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user), "role": user.role, "amr": ["pwd"], "exp": expires}, settings.jwt_secret, algorithm="HS256")


def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser:
    settings = get_settings()
    token = credentials.credentials if credentials else request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(401, "Authentication required")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        role = str(payload["role"])
        if role not in {"admin", "doctor"}:
            raise KeyError("role")
        return AuthenticatedUser(str(payload["sub"]), role)
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(401, "Invalid or expired token")


def require_admin(user: AuthenticatedUser = Depends(current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def require_collector(x_collector_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_collector_key, get_settings().collector_api_key):
        raise HTTPException(401, "Invalid collector key")

