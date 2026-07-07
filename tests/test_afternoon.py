from reporter.afternoon import _extract_keywords


class _FakeClient:
    def __init__(self, reply: str):
        self._reply = reply

    def chat(self, model, system, user, temperature=0.3):
        return self._reply


def test_strips_list_markers_but_keeps_digit_leading_keywords():
    # LLM 이 번호/불릿을 붙여도 마커만 제거하고, 숫자로 시작하는 종목/테마는 보존해야 한다
    reply = "1. 2차전지\n2) 5G\n- 4대금융지주\n• 삼성전자\n3분기 실적"
    keywords = _extract_keywords(_FakeClient(reply), "m", "briefing")
    assert keywords == ["2차전지", "5G", "4대금융지주", "삼성전자", "3분기 실적"]


def test_limits_to_five_keywords():
    reply = "\n".join(f"종목{i}" for i in range(10))
    keywords = _extract_keywords(_FakeClient(reply), "m", "briefing")
    assert len(keywords) == 5
