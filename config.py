"""Runtime configuration for the DFS client."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from exceptions import ConfigurationError

CHUNK_SIZE = 1024


@dataclass(frozen=True)
class StorageServer:
    """A storage node identified by a unique ID and its base URL."""

    id: str
    url: str


class Settings(BaseSettings):
    """Client settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None,
        populate_by_name=True,
        extra="ignore",
    )

    naming_url: str = Field(default="http://naming:8000", alias="NAMING_URL")
    replication_factor: int = Field(default=2, alias="REPLICATION_FACTOR")
    # Optional fallback only. Placement and chunk locations come from the naming
    # server; this is used solely to resolve URLs if the naming server omits them.
    storage_servers: str = Field(default="", alias="STORAGE_SERVERS")
    request_timeout: float = Field(default=10.0, alias="REQUEST_TIMEOUT")

    @field_validator("naming_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return str(value).rstrip("/")

    def parsed_storage_servers(self) -> list[StorageServer]:
        """
        Parse comma-separated storage URLs and assign unique IDs.
        IDs are based on the order (storage-1, storage-2, ...) to ensure uniqueness.
        """
        urls = [part.strip() for part in self.storage_servers.split(",") if part.strip()]
        if not urls:
            raise ConfigurationError("STORAGE_SERVERS must list at least one storage URL")
        if len(urls) < self.replication_factor:
            raise ConfigurationError(
                f"STORAGE_SERVERS must contain at least {self.replication_factor} URLs, "
                f"got {len(urls)}"
            )
        # Assign IDs based on order (1-indexed)
        return [
            StorageServer(id=f"storage-{i+1}", url=url.rstrip("/"))
            for i, url in enumerate(urls)
        ]

    def storage_url_map(self) -> dict[str, str]:
        """Map storage server IDs to base URLs (empty when no fallback is set).

        Locations normally come from the naming server; this fallback only
        matters if ``STORAGE_SERVERS`` is explicitly configured.
        """
        if not self.storage_servers.strip():
            return {}
        return {server.id: server.url for server in self.parsed_storage_servers()}


def get_settings() -> Settings:
    """Load settings from the environment."""
    return Settings()  # type: ignore[call-arg]