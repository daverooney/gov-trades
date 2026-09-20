"""Configuration, read from environment variables only.

The library never opens .env or a secrets API. Each runtime populates the
environment its own way (DESIGN-SENATE §8): scripts call ``load_dotenv``,
Actions injects secrets, the Colab notebook copies its secrets pane into
``os.environ``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

HOUSE_CLERK_BASE = "https://disclosures-clerk.house.gov/public_disc"


@dataclass(frozen=True)
class Settings:
    contact_email: str
    request_delay_seconds: float
    request_jitter_seconds: float
    request_timeout_seconds: float
    storage_local_root: Path
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    r2_account_endpoint: str | None
    r2_bucket: str | None

    @property
    def user_agent(self) -> str:
        return f"gov-trades/0.1 (+https://github.com/daverooney/gov-trades; {self.contact_email})"

    @property
    def r2_configured(self) -> bool:
        return all(
            (
                self.r2_access_key_id,
                self.r2_secret_access_key,
                self.r2_account_endpoint,
                self.r2_bucket,
            )
        )


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _opt(name: str) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or None


def load_settings() -> Settings:
    """Build Settings from the current environment.

    ``CONTACT_EMAIL`` is required: every request to a government site
    identifies who is asking.
    """
    contact = _opt("CONTACT_EMAIL")
    if not contact:
        raise RuntimeError("CONTACT_EMAIL is not set; see .env.example")
    root = _opt("STORAGE_LOCAL_ROOT")
    return Settings(
        contact_email=contact,
        request_delay_seconds=_float("REQUEST_DELAY_SECONDS", 2.0),
        request_jitter_seconds=_float("REQUEST_JITTER_SECONDS", 0.5),
        request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", 60.0),
        storage_local_root=Path(root) if root else PROJECT_ROOT / "data",
        r2_access_key_id=_opt("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_opt("R2_SECRET_ACCESS_KEY"),
        r2_account_endpoint=_opt("R2_ACCOUNT_ENDPOINT"),
        r2_bucket=_opt("R2_BUCKET"),
    )
