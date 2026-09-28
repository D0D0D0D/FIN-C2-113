# FIN-C2-113 — Unit Tests: RegulatorSourceService

from src.services.regulator_source_service import RegulatorSourceService
from tests.conftest import RecordingFetcher


class TestRegulatorSourceService:
    def test_default_registry_ships_five_regulators(self):
        service = RegulatorSourceService(fetcher=RecordingFetcher())
        assert set(service.known_regulators()) == {"FSA", "BOJ", "Basel", "SEC", "APRA"}

    def test_config_sources_extend_and_override_defaults(self):
        service = RegulatorSourceService(
            sources={"FSA": {"name": "FSA custom", "url": "https://custom.example/fsa"},
                     "ESMA": {"name": "ESMA", "url": "https://esma.example"}},
            fetcher=RecordingFetcher(),
        )
        assert service.sources["FSA"]["url"] == "https://custom.example/fsa"
        assert "ESMA" in service.known_regulators()

    def test_unknown_regulator_recorded_as_fetch_error(self):
        service = RegulatorSourceService(fetcher=RecordingFetcher())
        docs, errors = service.fetch_publications(["NOPE"])
        assert docs == []
        assert errors[0]["regulator"] == "NOPE"

    def test_per_regulator_failure_never_raises(self):
        fetcher = RecordingFetcher(fail_hosts=("sec.gov",))
        service = RegulatorSourceService(fetcher=fetcher)
        docs, errors = service.fetch_publications(["FSA", "SEC", "APRA"])
        assert {d["regulator"] for d in docs} == {"FSA", "APRA"}
        assert errors[0]["regulator"] == "SEC"

    def test_content_truncated(self):
        service = RegulatorSourceService(fetcher=lambda url: "x" * 100_000)
        docs, _ = service.fetch_publications(["FSA"])
        assert len(docs[0]["content"]) == 40_000


class TestLlmClientSeam:
    def test_unknown_provider_rejected(self):
        import pytest

        from shared.secrets import InMemoryProvider
        from src.services.llm_client import build_llm

        with pytest.raises(ValueError, match="unknown llm_provider"):
            build_llm({"llm_provider": "nonexistent"}, InMemoryProvider({}))

    def test_parse_json_content_variants(self):
        from src.services.llm_client import parse_json_content

        assert parse_json_content('{"a": 1}') == {"a": 1}
        assert parse_json_content('```json\n[1, 2]\n```') == [1, 2]
        assert parse_json_content('prose before {"a": 1} prose after') == {"a": 1}
        import pytest
        with pytest.raises(ValueError):
            parse_json_content("no json here")
