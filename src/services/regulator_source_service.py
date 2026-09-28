"""AgentCore Platform v1.0"""

# Live-fetch of regulator publications for FIN-C2-113.
#
# Contract (docs/02_design.md §Composition Pattern):
#   - Per-regulator transport failures are caught HERE and recorded as data
#     (fetch_errors) — they never propagate as exceptions, so the inner graph
#     keeps its "propagate" error strategy without aborting on partial failure.
#   - Only the caller decides what a total failure means (all sources failed).
#
# Source registry: the 5 default regulators ship in config/config.yaml
# (sources); _DEFAULT_SOURCES below is the fallback when config
# omits an entry. Users add/override sources via config only — no code change.

from __future__ import annotations

import time
import urllib.error
import urllib.request

_FETCH_TIMEOUT_SECONDS = 20
_MAX_CONTENT_CHARS = 40_000  # truncate raw pages before LLM normalization

_DEFAULT_SOURCES: dict[str, dict] = {
    "FSA": {
        "name": "金融庁 (Financial Services Agency)",
        "url": "https://www.fsa.go.jp/news/index.html",
    },
    "BOJ": {
        "name": "日本銀行 (Bank of Japan)",
        "url": "https://www.boj.or.jp/whatsnew/index.htm",
    },
    "Basel": {
        "name": "Basel Committee on Banking Supervision (BIS)",
        "url": "https://www.bis.org/bcbs/publications.htm",
    },
    "SEC": {
        "name": "U.S. Securities and Exchange Commission",
        "url": "https://www.sec.gov/rules-regulations",
    },
    "APRA": {
        "name": "Australian Prudential Regulation Authority",
        "url": "https://www.apra.gov.au/news-and-publications",
    },
}


class RegulatorSourceService:
    """Fetches current publications from regulator official sites on demand."""

    def __init__(self, sources: dict | None = None, fetcher=None, timeout: int = _FETCH_TIMEOUT_SECONDS):
        # config-provided sources are merged over defaults (user extension seam)
        merged = dict(_DEFAULT_SOURCES)
        merged.update(sources or {})
        self._sources = merged
        # injection seam for tests / alternative transports
        self._fetcher = fetcher or self._http_fetch
        self._timeout = timeout

    @property
    def sources(self) -> dict:
        return dict(self._sources)

    def known_regulators(self) -> list[str]:
        return list(self._sources.keys())

    def fetch_publications(self, regulators: list[str]) -> tuple[list[dict], list[dict]]:
        """Fetch publication pages for the requested regulators.

        Returns (documents, fetch_errors). Never raises for a single-regulator
        failure — the failure is recorded in fetch_errors and fetching continues.
        """
        documents: list[dict] = []
        fetch_errors: list[dict] = []
        for regulator in regulators:
            source = self._sources.get(regulator)
            if source is None:
                fetch_errors.append({"regulator": regulator, "reason": "unknown regulator (not in source registry)"})
                continue
            try:
                content = self._fetcher(source["url"])
                documents.append({
                    "regulator": regulator,
                    "source_name": source.get("name", regulator),
                    "url": source["url"],
                    "content": content[:_MAX_CONTENT_CHARS],
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            except Exception as e:  # noqa: BLE001 — partial failure is data, not an exception
                fetch_errors.append({"regulator": regulator, "reason": str(e)})
        return documents, fetch_errors

    def _http_fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "AgentCore-FIN-C2-113/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.URLError as e:
            raise RuntimeError(f"fetch failed: {e}") from e
