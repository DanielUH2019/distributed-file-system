"""Runtime configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Storage server settings validated at startup."""

    model_config = SettingsConfigDict(
        env_file=None,
        populate_by_name=True,
        extra="ignore",
    )

    storage_id: str = Field(alias="STORAGE_ID")
    storage_port: int = Field(default=9000, alias="STORAGE_PORT")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    naming_url: str | None = Field(default=None, alias="NAMING_URL")
    storage_url: str | None = Field(default=None, alias="STORAGE_URL")

    @field_validator("data_dir", mode="before")
    @classmethod
    def parse_data_dir(cls, value: str | Path) -> Path:
        return Path(value)

    def ensure_data_dir(self) -> None:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def self_url(self) -> str:
        """The URL peers use to reach this node.

        Falls back to ``http://{storage_id}:{storage_port}`` which resolves
        inside the compose network where the container hostname equals the id.
        """
        if self.storage_url:
            return self.storage_url.rstrip("/")
        return f"http://{self.storage_id}:{self.storage_port}"


def get_settings() -> Settings:
    """Load settings from the environment."""
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_data_dir()
    return settings
