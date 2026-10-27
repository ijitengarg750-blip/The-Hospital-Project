from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./hospital.db"
    storage_root: Path = Path("./clinical-data")
    doctor_username: str = "doctor"
    doctor_password: str = "change-me"
    admin_username: str = "admin"
    admin_password: str = "change-me-admin"
    jwt_secret: str = "development-only-change-me"
    access_token_minutes: int = 480
    session_cookie_name: str = "hospital_session"
    session_cookie_secure: bool = True
    mfa_required: bool = False
    auth_max_attempts: int = 5
    auth_window_minutes: int = 15
    max_upload_bytes: int = 2_000_000_000
    allowed_origins: str = "http://localhost:5173,http://localhost:8080"
    vnc_targets_file: Path | None = None
    vnc_gateway_client_cert_file: Path | None = None
    vnc_gateway_client_key_file: Path | None = None
    vnc_gateway_ca_cert_file: Path | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

