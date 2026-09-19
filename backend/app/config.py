from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# backend/.env — resolved absolutely so the app works from any working
# directory, not just backend/.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # --- Groq ---
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # --- Storage ---
    # On Render this points at the mounted disk (e.g. /var/data).
    # Locally it defaults to backend/data/.
    data_dir: str = str(Path(__file__).resolve().parent.parent / "data")

    # --- Chunking ---
    chunk_size: int = 1000          # characters per chunk
    chunk_overlap: int = 150        # characters of overlap between chunks

    # --- Retrieval ---
    top_k: int = 4                  # chunks pulled into the prompt
    # Chroma always returns top_k rows however poor the match, so a question
    # about nothing in the corpus still comes back with k "sources". Anything
    # below this cosine similarity is dropped as noise.
    min_similarity: float = 0.25
    max_upload_mb: int = 20

    # --- CORS ---
    # Comma separated list, or "*" for everything.
    cors_origins: str = "*"

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.data_path / 'app.db'}"

    @property
    def chroma_path(self) -> str:
        return str(self.data_path / "chroma")

    @property
    def upload_path(self) -> Path:
        p = self.data_path / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
