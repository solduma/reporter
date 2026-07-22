"""온톨로지 지표 그래프 순회.

- 비율 → 상위 계정(ratio.depends_on, required_accounts)
- 계정 → 하위 비율(account.ratios)·파생지표(account.affects)
- 계정 → 상위 계정(account.depends_on) / 하위 계정(account.children)
계산 순서 위상정렬·영향도 전파·결측 전파 분석에 사용.
"""

from __future__ import annotations

from collections import defaultdict, deque

from .models import Ontology


class Graph:
    """온톨로지 의존성 그래프."""

    def __init__(self, ontology: Ontology):
        self._ont = ontology

    def ratio_inputs(self, ratio_id: str) -> list[str]:
        """비율이 의존하는 계정 ID 집합(required_accounts + depends_on 중 계정)."""
        ratio = self._ont.ratio(ratio_id)
        if ratio is None:
            return []
        seen: dict[str, None] = {}
        for aid in (*ratio.required_accounts, *ratio.depends_on):
            if aid in self._ont.accounts and aid not in seen:
                seen[aid] = None
        return list(seen)

    def account_downstream_ratios(self, account_id: str) -> list[str]:
        """계정이 입력인 비율 ID(account.ratios)."""
        acc = self._ont.account(account_id)
        return list(acc.ratios) if acc else []

    def account_affects(self, account_id: str) -> list[str]:
        """계정이 영향하는 하향 파생지표·비율(account.affects)."""
        acc = self._ont.account(account_id)
        return list(acc.affects) if acc else []

    def upstream_accounts(self, account_id: str) -> list[str]:
        """계정의 상향 계정(account.depends_on + parent)."""
        acc = self._ont.account(account_id)
        if not acc:
            return []
        seen: dict[str, None] = {}
        for aid in (*acc.depends_on, *([acc.parent] if acc.parent else [])):
            if aid in self._ont.accounts and aid not in seen:
                seen[aid] = None
        return list(seen)

    def downstream_accounts(self, account_id: str) -> list[str]:
        """계정의 하향 계정(children)."""
        acc = self._ont.account(account_id)
        return [c for c in acc.children if c in self._ont.accounts] if acc else []

    def transitive_inputs(self, ratio_id: str) -> list[str]:
        """비율이 필요한 모든 기초 계정(의존 계정의 depends_on 재귀 전개)."""
        ratio = self._ont.ratio(ratio_id)
        if ratio is None:
            return []
        result: dict[str, None] = {}
        queue: deque[str] = deque(ratio.required_accounts)
        while queue:
            aid = queue.popleft()
            if aid in result or aid not in self._ont.accounts:
                continue
            result[aid] = None
            acc = self._ont.accounts[aid]
            queue.extend(acc.depends_on)
        return list(result)

    def ratios_depending_on(self, account_id: str) -> list[str]:
        """해 계정을 입력으로 쓰는 모든 비율(전 ratio 스캔)."""
        return [
            rid
            for rid, r in self._ont.ratios.items()
            if account_id in r.required_accounts or account_id in r.depends_on
        ]

    def account_to_ratios_index(self) -> dict[str, list[str]]:
        """계정 ID → 해당 계정을 사용하는 비율 ID 목록(역색인)."""
        idx: dict[str, list[str]] = defaultdict(list)
        for rid, r in self._ont.ratios.items():
            for aid in {*r.required_accounts, *r.depends_on}:
                idx[aid].append(rid)
        return {k: idx[k] for k in sorted(idx)}
