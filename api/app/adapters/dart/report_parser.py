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
from app.adapters.storage import minio_store

logger = logging.getLogger(__name__)

_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"

# MinIO 원문 zip 캐시 키 접두사. rcept_no 원문은 불변이므로 append-only(TTL 불필요).
_DOC_CACHE_PREFIX = "dart-doc/"


def _doc_cache_get(rcept_no: str) -> bytes | None:
    """MinIO 원문 zip 캐시 조회. 캐시 미설정·오류는 None(fail-open)."""
    try:
        return minio_store.get_bytes(f"{_DOC_CACHE_PREFIX}{rcept_no}.zip")
    except Exception as e:  # 캐시 장애가 수집을 막지 않는다(fail-open)
        logger.warning("dart doc cache get failed %s: %s", rcept_no, e)
        return None


def _doc_cache_put(rcept_no: str, data: bytes) -> None:
    try:
        minio_store.put_bytes(f"{_DOC_CACHE_PREFIX}{rcept_no}.zip", data)
    except Exception as e:
        logger.warning("dart doc cache put failed %s: %s", rcept_no, e)


def fetch_report_zip(api_key: str, rcept_no: str, session: requests.Session) -> bytes | None:
    """document.xml zip 원문(bytes)을 받는다. 실패 시 None.

    MinIO(dart-doc/{rcept_no}.zip) 캐시-aside — hit 시 DART 호출 없이 반환, miss 시
    다운로드 후 저장. 캐시 오류는 fail-open(다운로드만)이라 캐시 장애가 수집을 막지 않는다.
    """
    cached = _doc_cache_get(rcept_no)
    if cached is not None:
        return cached
    try:
        resp = dart_throttle.get(
            session, _DOCUMENT_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no}, timeout=60
        )
        resp.raise_for_status()
        if resp.content:
            _doc_cache_put(rcept_no, resp.content)
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


# ---- SCE(자본변동표) 파싱 ---------------------------------------------------
#
# DART fnlttSinglAcntAll CFS SCE 는 (a) 013(연결 미공시) 로 반환하지 않거나
# (b) 연결 BS/CIS 와 별도 SCE 를 같은 응답에 섞어 내는 등 신뢰할 수 없다(000890 실측).
# 공시 원문의 자본변동표 본표를 파싱한다. 원문이 진실.
#
# 테이블 구조(실측): TH 헤더(다단, colspan) + 데이터 행(좌정렬=라벨, 우정렬=값).
# 전기 블록 먼저, 각 블록 = 'YYYY.MM.DD (기초자본)' → 계정 행 → 'YYYY.MM.DD (기말자본)'.
# 연간 _00761(연결)/_00760(별도), 분기 단일파일은 섹션 제목으로 구분.

_SCE_TITLE_RE = re.compile(r"자\s*본\s*변\s*동\s*표")
# 구식 보고서(2016년경)는 기초/기말자본 라벨에 로마숫자 접두사를 단다 — 선택 허용(실측).
# 대형사(삼성전자 등) 분기/반기 보고서는 '(분기말자본)'·'(반기말자본)', 삼성전기는
# '(기초)'·'(기말)' — '분'/'반' 접두와 '자본' 접미사 모두 선택 허용.
_SCE_KIND_RE = re.compile(r"(\d{4}\.\d{1,2}\.\d{1,2})\s*[\(（]\s*(?:[Ⅰ-Ⅹ]+\.?)?\s*((?:분|반)?기(?:초|말)(?:자본|금액)?)\s*[\)）]")  # noqa: RUF001 (로마숫자 접두사 매칭 의도)
_SCE_TH_RE = re.compile(r"<TH([^>]*)>(.*?)</TH>", re.DOTALL)
_SCE_TR_RE = re.compile(r"<TR([^>]*)>(.*?)</TR>", re.DOTALL)

# 합계 열 라벨 → '연결/별도재무제표 [member]' 로 정규화(UI _clean_leaf 가 자본총계로 매핑).
_SCE_TOTAL_LEAVES = ("자본 합계", "자본합계", "합계")


def _is_consolidated_title(xml: str, pos: int) -> bool:
    """자본변동표 타이틀 직전(공백 제거)이 '연결'로 끝나면 연결 섹션."""
    pre = re.sub(r"\s+", "", xml[max(0, pos - 10):pos])
    return pre.endswith("연결")


def _find_sce_table(xml: str, want_consolidated: bool) -> tuple[int, int] | None:
    """연결/별도 자본변동표 본표 TABLE 의 (시작, 끝) 오프셋. 없으면 None.

    섹션 제목('연결 자본변동표'/'자본변동표') 이후 첫 '기초자본'+'기말자본' 을 담은 TABLE.
    목차 항목(.....) 은 제외. 분기 단일파일·연간 다중파일 모두 동작.
    """
    for m in _SCE_TITLE_RE.finditer(xml):
        pre = xml[max(0, m.start() - 80):m.start()]
        if "....." in pre:  # 목차 항목(점선 지시선) 제외
            continue
        if _is_consolidated_title(xml, m.start()) != want_consolidated:
            continue
        pos = m.end()
        for _ in range(20):
            ts = xml.find("<TABLE", pos)
            if ts == -1:
                break
            te = xml.find("</TABLE>", ts)
            if te == -1:
                break
            seg = xml[ts:te]
            # '기초'/'기말' — 삼성전기 등은 '(기초)'·'(기말)' 라벨(자본 접미사 없음).
            if "기초" in seg and "기말" in seg:
                return ts, te
            pos = te
    return None


def _sce_rows(xml: str, tstart: int, tend: int) -> list[tuple[str, list[str]]]:
    """자본변동표 데이터 행 재구성 — 좌정렬=새 행 라벨, 우정렬=값(빈 셀 포함, 위치 보존).

    우정렬 빈 셀('')도 값으로 남긴다 — 비지배지분이 없는 발행사(008700 실측)는 해당 열이
    빈 셀로 존재해, 건너뛰면 뒤 값들이 한 열씩 밀려 자본합계가 비지배지분으로 오정렬된다.
    """
    rows: list[tuple[str, list[str]]] = []
    for _pos, right, txt in _parse_cells(xml[tstart:tend]):
        if not right:
            if txt:  # 좌정렬 빈 셀은 라벨이 아니므로 스킵
                rows.append((txt, []))
        elif rows:
            rows[-1][1].append(txt)
    return rows


def _parse_sce_header(xml: str, tstart: int, tend: int, ncols: int) -> list[str]:
    """헤더 TH 다단 병합 → 컬럼 leaf 목록(innermost-wins).

    헤더를 계층 파티션으로 배치한다: 첫 행은 [0, 헤더폭) 전체, 이후 각 행은 **직전 행에서
    자신의 총 폭과 같은 span 의 셀**을 세분화한다(일치 셀 없으면 데이터 영역 전체 분할 —
    삼성전자·000890 의 지배기업지분 행). leaf = 데이터 열을 덮는 **최하위**(가장 구체) 행의
    라벨. 아래→위로 빈 셀만 채운다.

    중첩 그룹(실측): SK이노 연결 SCE 는 4단 — 자본금│기타불입자본[주식발행초과금·자기주식·
    기타·기타불입자본합계]│이익잉여금│기타자본구성요소│지배기업지분합계│비지배지분│자본합계.
    이때 leaf 행(기타불입자본 세분화)은 데이터 열 0 이 아니라 기타불입자본 그룹 아래에서
    시작한다 — 고정 '데이터 열 0 시작' 가정은 깨진다.
    """
    table = xml[tstart:tend]
    rows: list[list[tuple[str, int]]] = []
    for m in _SCE_TR_RE.finditer(table):
        tr = m.group(2)
        if "<TH" not in tr:
            continue
        row: list[tuple[str, int]] = []
        for c in _SCE_TH_RE.finditer(tr):
            col = re.search(r"COLSPAN\s*=\s*[\"']?(\d+)", c.group(1).upper())
            span = int(col.group(1)) if col else 1
            txt = re.sub(r"<[^>]+>", " ", c.group(2))
            txt = re.sub(r"\s+", " ", txt).strip()
            row.append((txt, span))
        rows.append(row)
    if not rows:
        return []
    header_width = max(sum(sp for _, sp in row) for row in rows)
    start = max(0, header_width - ncols)  # 과목 등 라벨 열 폭
    # 각 행의 셀 위치 [시작, 시작+span) 계산.
    placed: list[list[tuple[int, int, str]]] = []
    prev: list[tuple[int, int, str]] | None = None
    for ri, row in enumerate(rows):
        total = sum(sp for _, sp in row)
        if ri == 0:
            rstart = 0  # 첫 행: 라벨 셀 포함 [0, 헤더폭)
        else:
            match = next(((s, sp) for s, sp, _t in prev or [] if sp == total), None)
            rstart = match[0] if match else start  # 없으면 데이터 영역 전체 분할
        cells: list[tuple[int, int, str]] = []
        idx = rstart
        for txt, span in row:
            cells.append((idx, span, txt))
            idx += span
        placed.append(cells)
        prev = cells
    # 아래→위: 빈 셀만 채운다(innermost-wins).
    leaves = [""] * ncols
    for cells in reversed(placed):
        for cstart, span, txt in cells:
            for j in range(cstart, cstart + span):
                if start <= j < start + ncols and not leaves[j - start]:
                    leaves[j - start] = txt
    return leaves


def _split_sce_blocks(
    rows: list[tuple[str, list[str]]],
) -> list[tuple[tuple[int, int, int], list[tuple[str, list[str]]]]]:
    """데이터 행 → [(기말자본 날짜, 블록 행들)] 블록 목록.

    'YYYY.MM.DD (기초자본)' 이 블록 시작, '기말자본' 행이 블록 종료. 기초/기말 행의 값도
    데이터 행으로 남긴다(DART 형식은 기초/기말자본 아이템을 포함).
    """
    blocks: list[tuple[tuple[int, int, int], list[tuple[str, list[str]]]]] = []
    cur: list[tuple[str, list[str]]] | None = None
    for label, values in rows:
        m = _SCE_KIND_RE.search(label)
        if m:
            kind = m.group(2)
            if kind.startswith("기초"):  # 기초자본·기초 — 블록 시작
                cur = [(kind, values)]
            else:  # 기말자본·분기말자본·반기말자본·기말 — 블록 종료
                if cur is not None:
                    cur.append((kind, values))
                    d = tuple(int(x) for x in m.group(1).split("."))
                    blocks.append((d, cur))
                    cur = None
            continue
        if cur is not None:
            cur.append((label, values))
    return blocks


def _to_dart_items(
    block: list[tuple[str, list[str]]],
    leaves: list[str],
    table_type: str,
    unit_mult: int,
) -> list[dict]:
    """블록 행×열 → DART SCE 아이템 {name, amount, detail, sj_div}.

    합계 열 detail 은 '연결/별도재무제표 [member]' 로 정규화. 빈 셀('-') 생략(DART 와 동일).
    상위 그룹 leaf 의 ' 합계' 접미사는 DART leaf 와 맞추기 위해 제거(지배기업지분 합계 등).
    """
    total_detail = (
        "연결재무제표 [member]" if table_type == "consolidated" else "별도재무제표 [member]"
    )
    items: list[dict] = []
    for label, values in block:
        for i, v in enumerate(values):
            if i >= len(leaves):
                break
            amount = _to_num(v)
            if amount is None:
                continue
            leaf = leaves[i].strip()
            if leaf in _SCE_TOTAL_LEAVES:
                detail = total_detail
            else:
                leaf = re.sub(r"\s*합계$", "", leaf)
                detail = f"{leaf} [member]"
            items.append(
                {"name": label, "amount": amount * unit_mult, "detail": detail, "sj_div": "SCE"}
            )
    return items


def parse_sce_blocks(
    xml: str, want_consolidated: bool
) -> list[tuple[tuple[int, int, int], list[dict]]] | None:
    """연결/별도 자본변동표의 (기말자본 날짜, 아이템 목록) 블록들. 테이블 없으면 None."""
    span = _find_sce_table(xml, want_consolidated)
    if not span:
        return None
    tstart, tend = span
    rows = _sce_rows(xml, tstart, tend)
    max_values = max((len(v) for _, v in rows), default=0)
    leaves = _parse_sce_header(xml, tstart, tend, max_values)
    if not leaves:
        return None
    unit_mult = _resolve_unit_mult(xml, tstart)
    table_type = "consolidated" if want_consolidated else "separate"
    out: list[tuple[tuple[int, int, int], list[dict]]] = []
    for end_date, block in _split_sce_blocks(rows):
        out.append((end_date, _to_dart_items(block, leaves, table_type, unit_mult)))
    return out


def parse_sce_tables_from_zip(
    zip_bytes: bytes,
) -> list[tuple[str, list[tuple[tuple[int, int, int], list[dict]]]]]:
    """document.xml zip → [(연결|별도, [(기말자본 날짜, 아이템 목록)])] 목록.

    연간: _00761(연결)/_00760(별도) 우선, 파싱 실패 시 본문 파일로 폴백. 분기 단일파일은
    섹션 제목으로 연결/별도를 구분한다. 파싱 실패·테이블 없음 → 빈 리스트.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            files = {n: _decode_xml(zf.read(n)) for n in zf.namelist() if n.endswith(".xml")}
    except (zipfile.BadZipFile, KeyError):
        return []
    if not files:
        return []
    main = next((n for n in files if not re.search(r"_\d{5}\.xml$", n)), None)
    out: list[tuple[str, list[tuple[tuple[int, int, int], list[dict]]]]] = []
    for want_cons, suffix in ((True, "_00761.xml"), (False, "_00760.xml")):
        fn = next((n for n in files if n.endswith(suffix)), None)
        source = parse_sce_blocks(files[fn], want_cons) if fn else None
        # _00761/_00760 는 감사보고서 요약 재무제표만 담고 SCE 본표는 본문에만 있는 발행사
        # (SK이노 2025.12 실측) — 테이블은 찾았어도 블록이 없으면 본문으로 폴백.
        if not source and main:
            source = parse_sce_blocks(files[main], want_cons)
        if source:
            out.append(("consolidated" if want_cons else "separate", source))
    return out


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
