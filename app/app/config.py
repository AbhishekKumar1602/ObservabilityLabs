"""Typed configuration and secret-safe connection construction."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_name: str = "FastAPI Observability"
    app_version: str = "1.0.0"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    service_name: str = Field(default="fastapi-items", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="items", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    postgres_user: str = Field(default="items_app", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    postgres_password: SecretStr = Field(min_length=12)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_timeout_seconds: float = Field(default=3, ge=0.1, le=30)
    redis_host: str = "redis"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_password: SecretStr = Field(min_length=12)
    redis_pool_size: int = Field(default=10, ge=1, le=100)
    redis_timeout_seconds: float = Field(default=0.25, ge=0.05, le=5)
    cache_ttl_seconds: int = Field(default=30, ge=1, le=3600)
    dependency_probe_interval_seconds: float = Field(default=15, ge=1, le=300)
    startup_attempts: int = Field(default=5, ge=1, le=20)
    request_id_header: str = "X-Request-ID"
    metrics_enabled: bool = True
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    trace_sample_ratio: float = Field(default=1, ge=0, le=1)
    pyroscope_enabled: bool = True
    pyroscope_server_address: str = "http://pyroscope:4040"
    pyroscope_sample_rate: int = Field(default=100, ge=10, le=100)
    demo_enabled: bool = False

    @field_validator("request_id_header")
    @classmethod
    def header_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", value):
            raise ValueError("invalid request ID header name")
        if value.lower() in {"authorization", "cookie", "set-cookie", "content-length", "host"}:
            raise ValueError("request ID header must not replace a reserved header")
        return value

    @field_validator("otel_exporter_otlp_endpoint", "pyroscope_server_address")
    @classmethod
    def endpoint_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("expected HTTP(S) origin without credentials, query or path")
        return value.rstrip("/")

    @model_validator(mode="after")
    def production_hygiene(self) -> "Settings":
        if self.environment not in {"local", "test"}:
            if any(
                s.get_secret_value().startswith("local-only-") for s in (self.postgres_password, self.redis_password)
            ):
                raise ValueError("replace public demo credentials outside local/test")
            if self.demo_enabled:
                raise ValueError("demo CPU work must be disabled outside local/test")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
