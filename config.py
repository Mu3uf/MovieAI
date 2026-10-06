"""Central configuration. Everything comes from environment variables (.env)."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class Settings:
    SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()  # jobs only

    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
    TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile").strip()
    LLM_API_KEY = (os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY") or "").strip()
    LLM_TEMPERATURE = _float("LLM_TEMPERATURE", 0.2)

    HISTORY_MESSAGES = _int("HISTORY_MESSAGES", 6)
    EMBEDDINGS_ENABLED = _bool("EMBEDDINGS_ENABLED", False)
    CACHE_MAX_AGE_HOURS = _int("CACHE_MAX_AGE_HOURS", 36)
    PORT = _int("PORT", 5000)

    def missing(self) -> list[str]:
        """Variables the web app cannot run without."""
        required = {
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_ANON_KEY": self.SUPABASE_ANON_KEY,
            "TMDB_API_KEY": self.TMDB_API_KEY,
            "LLM_API_KEY": self.LLM_API_KEY,
        }
        return [k for k, v in required.items() if not v]


settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full URLs (incl. TMDB api_key) at INFO - silence it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
