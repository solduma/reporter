"""synthesize_digest / synthesize_entity 단위 테스트 — GLM 응답 목킹."""

from reporter import analyzer
from reporter.models import Report


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, model, system, user, temperature=0.3):
        return self._reply


def _reports(n, views=None):
    views = views or [10] * n
    out = []
    for i in range(n):
        r = Report(category="market_info", title=f"리포트{i+1}", broker="삼성증권",
                   date="26.07.08", views=views[i], read_url=f"http://x/{i+1}")
        r.summary = f"요약{i+1}"
        out.append(r)
    return out


def test_digest_parses_sources_line_and_strips_it():
    reports = _reports(4)
    reply = "장문 종합 본문입니다.\n두 번째 줄.\nSOURCES: S3,S1"
    d = analyzer.synthesize_digest(_FakeClient(reply), "m", reports)
    # 본문에서 SOURCES 줄은 제거
    assert "SOURCES" not in d.text
    assert "장문 종합 본문" in d.text
    # 인용 지목 S3, S1 이 앞에 오고, 5개 미만이라 나머지는 조회수 폴백으로 보충
    assert d.sources[0].title == "리포트3"
    assert d.sources[1].title == "리포트1"
    assert len(d.sources) == 4  # 총 4건이라 4개 전부


def test_digest_fallback_to_views_when_no_sources_line():
    # SOURCES 줄 없으면 조회수 상위로 폴백
    reports = _reports(6, views=[1, 2, 3, 4, 5, 6])
    d = analyzer.synthesize_digest(_FakeClient("SOURCES 없는 본문"), "m", reports)
    assert len(d.sources) == 5
    # 조회수 최상위(6)가 첫 소스
    assert d.sources[0].views == 6


def test_digest_ignores_hallucinated_ids():
    reports = _reports(3)
    # S9 는 범위 밖(hallucinated) → 무시, 유효한 S2 만 채택 후 폴백 보충
    d = analyzer.synthesize_digest(_FakeClient("본문\nSOURCES: S9,S2"), "m", reports)
    assert d.sources[0].title == "리포트2"
    assert all(s in reports for s in d.sources)


def test_entity_single_report_returns_its_summary():
    reports = _reports(1)
    # 1건이면 GLM 호출 없이 그 요약 그대로
    assert analyzer.synthesize_entity(_FakeClient("무시됨"), "m", reports) == "요약1"


def test_entity_multiple_calls_glm():
    reports = _reports(2)
    assert analyzer.synthesize_entity(_FakeClient("합본 종합"), "m", reports) == "합본 종합"


def test_forecast_returns_briefing():
    reports = _reports(3)
    b = analyzer.synthesize_forecast(_FakeClient("🔮 오늘의 핵심\n예상 본문"), "m", reports)
    assert "예상 본문" in b.text
    assert b.report_count == 3
    assert b.categories == ["market_info"]


def test_forecast_prompt_includes_summaries_and_forecast_framing():
    captured = {}

    class _Capture:
        def chat(self, model, system, user, temperature=0.3):
            captured["system"] = system
            captured["user"] = user
            return "브리핑"

    analyzer.synthesize_forecast(_Capture(), "m", _reports(2))
    assert "요약1" in captured["user"] and "요약2" in captured["user"]
    # 시스템 프롬프트가 '오늘 전망/예상' 관점인지.
    assert "전망" in captured["system"] or "예상" in captured["system"]


def test_closing_review_returns_briefing():
    b = analyzer.synthesize_closing_review(_FakeClient("📉 오늘 마감\n리뷰 본문"), "m", _reports(2))
    assert "리뷰 본문" in b.text
    assert b.report_count == 2


def test_closing_review_framing_is_review_plus_tomorrow():
    captured = {}

    class _Capture:
        def chat(self, model, system, user, temperature=0.3):
            captured["system"] = system
            return "브리핑"

    analyzer.synthesize_closing_review(_Capture(), "m", _reports(2))
    # 마감 리뷰 + 내일 전망 관점인지.
    assert "마감" in captured["system"] and "내일" in captured["system"]
