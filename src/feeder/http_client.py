"""WiFi HTTP client for the UNO R4 conveyor firmware."""

from __future__ import annotations

import os
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "http://192.168.4.30"
DEFAULT_TIMEOUT_S = 3.0


class ConveyorError(RuntimeError):
    """Raised when the conveyor HTTP API fails."""


def feeder_base_url() -> str:
    return os.getenv("FEEDER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class ConveyorClient:
    """Thin wrapper around firmware routes: GET /start /stop /advance."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or feeder_base_url()).rstrip("/")
        self.timeout_s = timeout_s
        self._session = session or requests.Session()

    def _get(self, path: str) -> None:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            resp = self._session.get(url, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise ConveyorError(f"Conveyor unreachable at {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ConveyorError(f"Conveyor {path} returned HTTP {resp.status_code}")

    def ping(self) -> str:
        """Hit the control page; returns a short OK string or raises."""
        url = self.base_url + "/"
        try:
            resp = self._session.get(url, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise ConveyorError(f"Conveyor unreachable at {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ConveyorError(f"Conveyor ping returned HTTP {resp.status_code}")
        return f"OK — {self.base_url}"

    def start(self) -> None:
        self._get("/start")

    def stop(self) -> None:
        self._get("/stop")

    def advance(self) -> None:
        self._get("/advance")
