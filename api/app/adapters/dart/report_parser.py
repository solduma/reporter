"""DART 정규공시 원문(document.xml)에서 현금흐름표 감가상각비+무형자산상각비 파싱.

구조화 API(fnlttSinglAcntAll)는 대형사 현금흐름표의 D&A add-back 을 담지 않아 EBITDA 가
과소계산된다(리서치 확인: 하이닉스 판관비 196십억 vs 현금흐름표 13.1조). 이 모듈은 사업/반기/
분기 보고서 원문 XML 에서 현금흐름표 감가상각·무형상각 당기값을 원 단위로 추출한다. 신뢰
불가(recon 주석 없음·은행·성격별 note-only)면 None 을 돌려 오탐을 피한다.

파싱 전략(실측 12종목x3보고서=36 검증, 28/36 검출):
1) 연결(_00761) 파일 우선. 반기/분기 단일파일은 첫 '연 결 현 금 흐 름 표' 본표(목차 제외)
   이후 구간으로 스코프 제한.
2) <TD>·<TE> 셀 모두 파싱(발행사별 상이).
3) 앵커: '현금흐름표' 문자열이 아니라 순이익+조정(가감) recon 블록. D&A 를 가장 많이 담은
   블록 채택.
4) 라벨 variant 매칭 + 제외어(누계액·부인액·판관비·성격별 배분 등)로 오탐 차단.
5) 값 위치 최근접 '(단위 : 원|천원|백만원)' 선언으로 원 단위 정규화.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

import requests

from app.adapters.dart import throttle as dart_throttle

logger = logging.getLogger(__name__)

_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"


def fetch_report_zip(api_key: str, rcept_no: str, session: requests.Session) -> bytes | None:
    """document.xml zip 원문(bytes)을 받는다. 실패 시 None."""
    try:
        resp = dart_throttle.get(
            session, _DOCUMENT_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=60
        )
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        logger.warning("dart document fetch failed %s: %s", rcept_no, e)
        return None


def _decode_xml(raw: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


_CELL_RE = re.compile(r"<(TD|TE)([^>]*)>(.*?)</(TD|TE)>", re.DOTALL)


def _parse_cells(xml: str) -> list[tuple[int, bool, str]]:
    """(문자오프셋, 우측정렬여부, 셀텍스트) 리스트. 우측정렬은 셀 속성으로 판정."""
    out = []
    for m in _CELL_RE.finditer(xml):
        txt = re.sub(r"<[^>]+>", " ", m.group(3))
        txt = re.sub(r"\s+", " ", txt).strip()
        right = "RIGHT" in m.group(2).upper()
        out.append((m.start(), right, txt))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _to_num(t: str) -> int | None:
    """'1,234' / '(1,234)'(음수) / 전각공백 → int. 파싱 불가면 None."""
    t = t.replace(",", "").replace("　", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if re.fullmatch(r"-?\d+", t):
        v = int(t)
        return -v if neg else v
    return None


# 오탐 유발 라벨(정규화 후 부분일치 시 스킵): BS 잔액·세무조정·기능별 배분·주석 서술 등.
_EXCLUDE = (
    "누계액", "부인액", "개시시점", "부문", "판관비", "회수", "손상", "차손",
    "적정성", "위험회피", "에대한기술", "전액", "차감한",
)


def _classify(label: str) -> str | None:
    """라벨을 tangible(감가상각비)·intangible(무형상각)·combined(합산)·None 으로 분류."""
    n = _norm(label)
    if any(x in n for x in _EXCLUDE):
        return None
    # 성격별(기능별) 배분표 '감가상각비, 유형자산' 류(쉼표) → 스킵.
    if "감가상각비," in label or "감가상각비 ," in label:
        return None
    combined = (
        "감가상각비및무형자산상각비" in n or "감가상각비와무형자산상각비" in n
        or "감가상각및무형자산상각" in n or "감가상각비무형자산상각비" in n
    )
    if combined:
        return "combined"
    if "무형자산상각" in n and "감가상각" not in n:
        return "intangible"
    if "감가상각비" in n or "감가상각비에대한조정" in n:
        return "tangible"
    return None


def _extract(cells: list[tuple[int, bool, str]]):
    """셀 스코프에서 (tangible, intangible, combined) 각 최대 add-back. 원소는 (금액, pos)|None.

    라벨 셀 다음 첫 우측정렬 숫자 = 당기값(CF 조정표는 당기가 항상 첫 열 — 실측 확인).
    """
    tan = intan = comb = None
    for i, (_pos, right, txt) in enumerate(cells):
        if right or not txt:
            continue
        cat = _classify(txt)
        if not cat:
            continue
        val = None
        for j in range(i + 1, min(i + 6, len(cells))):
            _, _r2, t2 = cells[j]
            v = _to_num(t2)
            if v is not None:
                val = v
                break
            if _norm(t2) and _classify(t2):  # 다음 라벨을 만나면 중단
                break
        if val is None:
            continue
        cand = (abs(val), _pos)
        if cat == "tangible":
            tan = cand if tan is None else max(tan, cand, key=lambda x: x[0])
        elif cat == "intangible":
            intan = cand if intan is None else max(intan, cand, key=lambda x: x[0])
        elif cat == "combined":
            comb = cand if comb is None else max(comb, cand, key=lambda x: x[0])
    return tan, intan, comb


_NI_ANCHORS = (
    "당기순이익", "당기순손실", "당기순손익", "분기순이익", "반기순이익",
    "연결분기순이익", "연결반기순이익", "연결당기순이익", "연결당기순손익",
    "법인세비용차감전순이익", "법인세비용차감전순손익", "법인세비용차감전계속영업이익",
)
_RECON_MARKERS = ("조정", "가감", "조정사항")


def _all_recon_blocks(cells: list[tuple[int, bool, str]]) -> list[list[tuple[int, bool, str]]]:
    """순이익 앵커 셀(선행 열거자 제거) + 다음 60셀 내 조정 마커 → 블록(+220셀)."""
    blocks = []
    for i, (_pos, _right, txt) in enumerate(cells):
        n = _norm(re.sub(r"^[0-9]+\.|^[가-힣]\.", "", txt))
        if any(a in n for a in _NI_ANCHORS):
            window = cells[i:i + 60]
            if any(any(mk in _norm(t) for mk in _RECON_MARKERS) for _, _, t in window):
                blocks.append(cells[i:i + 220])
    return blocks


def _best_recon(cells):
    """D&A 를 가장 많이 담은 recon 블록 채택. (tan, intan, comb) — 각 (금액, pos)|None."""
    best = (None, None, None)
    best_score = -1
    for block in _all_recon_blocks(cells):
        tan, intan, comb = _extract(block)
        score = (tan is not None) + (intan is not None) + (comb is not None)
        if score > best_score:
            best_score = score
            best = (tan, intan, comb)
    return best


# 긴 토큰부터('원'은 '백만원'/'천원'의 부분문자열).
_UNIT_TOKENS = (("십억원", 1_000_000_000), ("백만원", 1_000_000), ("천원", 1_000), ("원", 1))
_UNIT_RE = re.compile(r"\(단위\s*[:：]\s*([^)]+?)\)")  # noqa: RUF001 (전각콜론 매칭 의도)


def _resolve_unit_mult(xml: str, before_pos: int) -> int:
    """before_pos 이전 최근접 '(단위 : XXX)' 선언의 배수. 없으면 1(원)."""
    best_mult = 1
    best_at = -1
    for m in _UNIT_RE.finditer(xml, 0, before_pos):
        decl = m.group(1)
        for token, mult in _UNIT_TOKENS:
            if token in decl:
                if m.start() > best_at:
                    best_at = m.start()
                    best_mult = mult
                break
    return best_mult


def _pick_file(files: dict[str, str]) -> str:
    """연결(_00761) 우선. 없으면 첫 파일."""
    for n in files:
        if n.endswith("_00761.xml"):
            return n
    return next(iter(files))


def _scope_consolidated_single(xml: str) -> str:
    """단일파일: 첫 '연결 현금흐름표' 본표(목차 제외) 이후 구간(연결 CF·조정주석 우선)."""
    for m in re.finditer(r"연\s*결\s*현\s*금\s*흐\s*름\s*표", xml):
        pre = xml[max(0, m.start() - 80):m.start()]
        if "ATOCID" in pre or "....." in pre:  # 목차 항목 제외
            continue
        return xml[m.start():m.start() + 1_500_000]
    return xml


# 유형·무형자산 주석 상각비 라벨(정확히 이 라벨 셀만 — 누계·부문 등 오탐은 _classify/_EXCLUDE 로 걸러짐).
_NOTE_DA_LABELS = ("감가상각비", "무형자산상각비")


def _note_da_fallback(cells: list[tuple[int, bool, str]], xml: str, scope_start: int) -> int | None:
    """CF recon 에서 D&A 를 못 찾을 때(대형사: 조정을 요약하고 상각비를 유형·무형자산 주석으로 뺌),
    유형·무형자산 주석의 '당기'(첫 등장) 감가상각비+무형자산상각비를 합산해 D&A 를 근사한다.

    주석 표는 당기→전기 순이라 각 라벨의 첫 등장이 당기값. 라벨 다음 첫 우측정렬 숫자 x 단위배수.
    감가상각비/무형자산상각비 각각 최초 1회만 취해 전기·기능별 배분표 중복합산을 막는다.
    """
    taken: dict[str, int] = {}
    for i, (pos, right, txt) in enumerate(cells):
        if right:
            continue
        label = txt.strip()
        if label not in _NOTE_DA_LABELS or label in taken:
            continue
        for j in range(i + 1, min(i + 6, len(cells))):
            v = _to_num(cells[j][2])
            if v is not None:
                taken[label] = abs(v) * _resolve_unit_mult(xml, scope_start + pos + 1)
                break
    total = sum(taken.values())
    return total or None


def parse_cf_depreciation(zip_bytes: bytes) -> int | None:
    """document.xml zip → 현금흐름표 감가상각비+무형자산상각비 당기값(원). 신뢰불가 시 None.

    감가상각비만·무형상각비만 있으면 있는 것만, 둘 다 없고 합산 라벨만 있으면 합산값 사용.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            files = {n: _decode_xml(zf.read(n)) for n in zf.namelist() if n.endswith(".xml")}
    except (zipfile.BadZipFile, KeyError):
        return None
    if not files:
        return None

    fn = _pick_file(files)
    xml = files[fn]
    if fn.endswith("_00761.xml"):
        scope, scope_start = xml, 0
    else:
        scope = _scope_consolidated_single(xml)
        scope_start = xml.find(scope[:200]) if scope is not xml else 0
    cells = _parse_cells(scope)
    tan, intan, comb = _best_recon(cells)
    if tan is None and intan is None and comb is None:
        # CF recon 실패 — 대형사(삼성 등)는 조정을 요약하고 상각비를 유형·무형자산 주석으로 뺀다.
        # 파일 전체 셀에서 주석 상각비를 fallback 으로 근사(scope 는 CF 본표 구간이라 전체 재파싱).
        full_cells = _parse_cells(xml)
        return _note_da_fallback(full_cells, xml, 0)

    total = 0
    use_combined = tan is None and intan is None
    for part in ((comb,) if use_combined else (tan, intan)):
        if part is None:
            continue
        amount, pos = part
        total += amount * _resolve_unit_mult(xml, scope_start + pos + 1)
    return total or None


# D&A 가 매출의 이 배수를 넘으면 오파싱(누계·부문 배분·잘못된 셀·단위 오인)으로 본다. 자본집약
# 업종도 감가상각이 매출을 크게 넘지 않는다(설비 상각이 매출 초과면 사업 지속 불가) — 보수적으로 8배.
_DA_REVENUE_MAX_RATIO = 8.0


def plausible_depreciation(dep: float | None, revenue: float | None) -> float | None:
    """감가상각비(원)가 매출(원) 대비 비현실적으로 크면(오파싱 의심) None. 둘 다 같은 원 단위 전제.

    revenue 결측이면 검증 불가라 그대로 통과(다른 지표로 판단). dep 음수·0 은 상위에서 처리."""
    if dep is None or revenue is None or revenue <= 0:
        return dep
    if abs(dep) > revenue * _DA_REVENUE_MAX_RATIO:
        logger.warning("implausible D&A %s vs revenue %s (ratio %.0f) — 오파싱으로 폐기",
                       dep, revenue, abs(dep) / revenue)
        return None
    return dep
