# FIN-C2-113 — Unit Tests: inner research nodes
# ParamsHydrate / LiveSourceFetch / ResultNormalize / InstitutionFilter / DeadlineScore

import json
from datetime import date

from framework.schemas.agent_status import AgentStatus

from src.nodes.deadline_score_node import DeadlineScoreNode
from src.nodes.institution_filter_node import InstitutionFilterNode
from src.nodes.live_source_fetch_node import LiveSourceFetchNode
from src.nodes.params_hydrate_node import ParamsHydrateNode
from src.nodes.result_normalize_node import ResultNormalizeNode
from src.services.regulator_source_service import RegulatorSourceService
from tests.conftest import FakeLLM, RecordingFetcher


def _base_state(**overrides):
    state = {"node_history": [], "error_log": [], "status": AgentStatus.SUCCESS.value}
    state.update(overrides)
    return state


class TestParamsHydrateNode:
    def test_hydrates_structured_params(self):
        payload = {"regulators": ["FSA"], "domains": ["capital"],
                   "institution_type": "mega-bank", "output_format": "brief"}
        result = ParamsHydrateNode().execute(_base_state(user_input=json.dumps(payload)))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["regulators"] == ["FSA"]
        assert result["refine_rounds"] == 0

    def test_invalid_json_errors(self):
        result = ParamsHydrateNode().execute(_base_state(user_input="not-json"))
        assert result["status"] == AgentStatus.ERROR.value

    def test_missing_keys_error(self):
        result = ParamsHydrateNode().execute(_base_state(user_input='{"regulators": []}'))
        assert result["status"] == AgentStatus.ERROR.value
        assert "missing keys" in result["error_log"][0]

    def test_upstream_error_passthrough(self):
        result = ParamsHydrateNode().execute(_base_state(status=AgentStatus.ERROR.value))
        assert result == {}


class TestLiveSourceFetchNode:
    def test_fetches_requested_regulators(self):
        fetcher = RecordingFetcher()
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        result = node.execute(_base_state(regulators=["FSA", "BOJ"], fetched_documents=[], fetch_errors=[]))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert {d["regulator"] for d in result["fetched_documents"]} == {"FSA", "BOJ"}
        assert result["fetch_errors"] == []

    def test_partial_failure_is_data_not_exception(self):
        fetcher = RecordingFetcher(fail_hosts=("boj.or.jp",))
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        result = node.execute(_base_state(regulators=["FSA", "BOJ"], fetched_documents=[], fetch_errors=[]))
        assert result["status"] == AgentStatus.SUCCESS.value  # continues on partial failure
        assert [d["regulator"] for d in result["fetched_documents"]] == ["FSA"]
        assert result["fetch_errors"][0]["regulator"] == "BOJ"

    def test_all_sources_failed_is_error(self):
        fetcher = RecordingFetcher(fail_hosts=("fsa.go.jp",))
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        result = node.execute(_base_state(regulators=["FSA"], fetched_documents=[], fetch_errors=[]))
        assert result["status"] == AgentStatus.ERROR.value

    def test_refine_round_fetches_only_new_scope(self):
        fetcher = RecordingFetcher()
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        already = [{"regulator": "FSA", "url": "u", "content": "c", "fetched_at": "t"}]
        result = node.execute(_base_state(regulators=["FSA", "BOJ"],
                                          fetched_documents=already, fetch_errors=[]))
        assert len(fetcher.urls) == 1 and "boj" in fetcher.urls[0]
        assert {d["regulator"] for d in result["fetched_documents"]} == {"FSA", "BOJ"}

    def test_stale_fetch_error_cleared_after_successful_retry(self):
        """F-02: a regulator that failed earlier but succeeds on retry must not
        keep its stale failure record in fetch_errors (coverage accuracy)."""
        fetcher = RecordingFetcher()  # succeeds now
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        state = _base_state(
            regulators=["FSA", "BOJ"],
            fetched_documents=[{"regulator": "FSA", "url": "u", "content": "c", "fetched_at": "t"}],
            fetch_errors=[{"regulator": "BOJ", "reason": "timeout round 1"}],
        )
        result = node.execute(state)
        assert {d["regulator"] for d in result["fetched_documents"]} == {"FSA", "BOJ"}
        assert result["fetch_errors"] == []  # BOJ recovered → stale error dropped

    def test_repeated_failure_keeps_single_latest_error(self):
        fetcher = RecordingFetcher(fail_hosts=("boj.or.jp",))
        node = LiveSourceFetchNode(service=RegulatorSourceService(fetcher=fetcher))
        state = _base_state(
            regulators=["FSA", "BOJ"],
            fetched_documents=[{"regulator": "FSA", "url": "u", "content": "c", "fetched_at": "t"}],
            fetch_errors=[{"regulator": "BOJ", "reason": "timeout round 1"}],
        )
        result = node.execute(state)
        boj_errors = [e for e in result["fetch_errors"] if e["regulator"] == "BOJ"]
        assert len(boj_errors) == 1  # replaced, not accumulated
        assert "round 1" not in boj_errors[0]["reason"]


class TestResultNormalizeNode:
    def test_normalizes_documents_via_llm(self):
        node = ResultNormalizeNode(llm=FakeLLM())
        docs = [{"regulator": "FSA", "source_name": "FSA", "url": "https://www.fsa.go.jp/x",
                 "content": "page", "fetched_at": "t"}]
        result = node.execute(_base_state(domains=["capital"], fetched_documents=docs))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["normalized_records"][0]["issuer"] == "FSA"

    def test_no_llm_errors(self):
        result = ResultNormalizeNode(llm=None).execute(_base_state(domains=[], fetched_documents=[]))
        assert result["status"] == AgentStatus.ERROR.value

    def test_unparseable_llm_output_errors(self):
        node = ResultNormalizeNode(llm=FakeLLM(broken="normalize"))
        docs = [{"regulator": "FSA", "url": "u", "content": "c", "fetched_at": "t"}]
        result = node.execute(_base_state(domains=[], fetched_documents=docs))
        assert result["status"] == AgentStatus.ERROR.value


class TestInstitutionFilterNode:
    RECORDS = [{"issuer": "FSA", "title": "a"}, {"issuer": "SEC", "title": "b"}, {"issuer": "APRA", "title": "c"}]

    def test_filters_by_institution_type(self):
        result = InstitutionFilterNode().execute(
            _base_state(institution_type="regional-bank", normalized_records=self.RECORDS))
        assert [r["issuer"] for r in result["filtered_records"]] == ["FSA"]

    def test_unspecified_keeps_all(self):
        result = InstitutionFilterNode().execute(
            _base_state(institution_type="unspecified", normalized_records=self.RECORDS))
        assert len(result["filtered_records"]) == 3


class TestDeadlineScoreNode:
    def test_urgency_windows(self):
        node = DeadlineScoreNode(today=date(2026, 7, 2))
        records = [
            {"title": "due-now", "deadline": "2026-07-20"},    # 18d → p1
            {"title": "due-soon", "deadline": "2026-09-15"},   # 75d → p2
            {"title": "far", "deadline": "2027-06-01"},        # >90d → p3
            {"title": "none", "deadline": None},               # → p3
        ]
        result = node.execute(_base_state(filtered_records=records))
        urgencies = {r["title"]: r["deadline_urgency"] for r in result["scored_records"]}
        assert urgencies == {"due-now": "p1", "due-soon": "p2", "far": "p3", "none": "p3"}

    def test_unparseable_deadline_is_p3(self):
        node = DeadlineScoreNode(today=date(2026, 7, 2))
        result = node.execute(_base_state(filtered_records=[{"title": "x", "deadline": "TBD"}]))
        assert result["scored_records"][0]["deadline_urgency"] == "p3"
