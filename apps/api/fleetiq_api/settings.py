"""Explicit runtime configuration; no usable credentials in defaults."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="forbid", hide_input_in_errors=True
    )
    project_root: Path = ROOT
    database_url: SecretStr
    worker_database_url: SecretStr
    migration_database_url: SecretStr
    test_admin_database_url: SecretStr
    mlflow_database_url: SecretStr
    db_bootstrap_admin_url: SecretStr | None = None
    db_port: int = Field(default=5433, ge=1024, le=65535)
    db_admin_password_file: Path | None = None
    db_roles_file: Path | None = None
    artifact_root: Path
    report_root: Path
    model_bundle_path: Path
    auth_issuer: str = Field(min_length=1)
    auth_audience: str = Field(min_length=1)
    auth_key_id: str = Field(min_length=1)
    auth_private_key_path: Path
    auth_public_key_path: Path
    auth_cookie_secure: bool | None = None
    inference_workload_token: SecretStr = Field(min_length=32)
    allowed_origins: list[str]
    worker_concurrency: int = Field(default=1, ge=1, le=16)
    inference_timeout_seconds: float = Field(default=10, gt=0, le=120)
    source_track: Literal["cmapss_benchmark", "synthetic_engine_demo"]
    replay_source: Path

    @field_validator(
        "database_url",
        "worker_database_url",
        "migration_database_url",
        "test_admin_database_url",
        "mlflow_database_url",
        "db_bootstrap_admin_url",
    )
    @classmethod
    def database_driver(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("database URL must use the PostgreSQL psycopg driver")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def explicit_origins(cls, values: list[str]) -> list[str]:
        if not values or any(v == "*" or not v.startswith(("http://", "https://")) for v in values):
            raise ValueError("explicit HTTP origins are required; wildcard origins are forbidden")
        return values

    @model_validator(mode="after")
    def resolve_paths(self):
        self.project_root = self.project_root.resolve()
        for name in (
            "artifact_root",
            "report_root",
            "model_bundle_path",
            "auth_private_key_path",
            "auth_public_key_path",
            "replay_source",
            "db_admin_password_file",
            "db_roles_file",
        ):
            p = getattr(self, name)
            if p is None:
                continue
            setattr(
                self,
                name,
                (self.project_root / p).resolve() if not p.is_absolute() else p.resolve(),
            )
        if not self.report_root.is_relative_to(self.project_root / "docs"):
            raise ValueError("reports must stay under ignored docs")
        if not self.artifact_root.is_relative_to(self.project_root / "artifacts"):
            raise ValueError("artifacts must stay under ignored artifacts")
        return self

    def secret_values(self) -> tuple[str, ...]:
        return tuple(
            v.get_secret_value() for v in self.__dict__.values() if isinstance(v, SecretStr)
        )
