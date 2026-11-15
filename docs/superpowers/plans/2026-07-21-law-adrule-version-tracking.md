# 법령/행정규칙 버전관리 + 신구대조 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** law/adrule 문서에 안정 ID(법령ID/행정규칙ID)를 붙이고, 일일 배치로 "현재 시행 버전"을 자동 재계산하며, 승격 시 조문 단위 신구대조(update_summary)를 생성한다.

**Architecture:** law.go.kr 상세 API로 안정 ID를 조회하는 공유 헬퍼(`lawgokr_stable_id.py`) → 크롤 시점에 각 스크래퍼가 이 안정 ID를 저장 → 별도의 일일 Prefect flow(`refresh_current_status_flow`)가 DB 메타데이터만으로 그룹핑/승격 판단(순수 함수 `refresh_promotion.py`)을 수행하고 `ArticleDiffBuilder`로 조문 diff를 생성 → 1회성 backfill 스크립트로 기존 문서에 안정 ID를 채운다. 크롤러(law/adrule scraper.py)는 더 이상 승격/비활성화를 직접 하지 않는다 — 이 책임은 전부 일일 flow로 이관한다.

**Tech Stack:** Python, Prefect 3, PyMongo(`UnifiedDocumentRepository`), requests(law.go.kr DRF API), unittest(모킹은 `unittest.mock`, 이 저장소는 pytest/mongomock을 쓰지 않음 — 순수 로직은 실제 함수로, DB 함수는 `tests/test_provenance_migration.py`와 동일한 FakeCollection 패턴으로 테스트).

**참조 스펙:** `docs/superpowers/specs/2026-07-17-law-adrule-version-tracking-design.md`

**범위 밖 (별도 계획):** case/interpretation/judgment/constitutional_decc/legislation_expc/admin_decc/decision/mediation_case 8종 PK 표준화는 `docs/superpowers/specs/2026-07-20-simple-collections-pk-standardization-design.md`에서 이 계획과 별도로 진행한다.

---

## Task 1: law.go.kr 안정 ID 조회 헬퍼

**Files:**
- Create: `scripts/core/lawgokr_stable_id.py`
- Test: `tests/test_lawgokr_stable_id.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_lawgokr_stable_id.py`:
```python
import unittest
from unittest.mock import patch, MagicMock

from scripts.core.lawgokr_stable_id import fetch_stable_law_id, fetch_stable_adrule_id


class FetchStableLawIdTest(unittest.TestCase):
    @patch("scripts.core.lawgokr_stable_id.requests.get")
    def test_returns_law_id_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"기본정보": {"법령ID": "001872"}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_stable_law_id("265959")

        self.assertEqual(result, "001872")
        mock_get.assert_called_once()
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["target"], "law")
        self.assertEqual(called_params["MST"], "265959")

    @patch("scripts.core.lawgokr_stable_id.requests.get")
    def test_returns_none_on_missing_field(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"기본정보": {}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.assertIsNone(fetch_stable_law_id("265959"))

    @patch("scripts.core.lawgokr_stable_id.requests.get")
    def test_returns_none_on_request_exception(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        self.assertIsNone(fetch_stable_law_id("265959"))


class FetchStableAdruleIdTest(unittest.TestCase):
    @patch("scripts.core.lawgokr_stable_id.requests.get")
    def test_returns_adrule_id_on_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"행정규칙기본정보": {"행정규칙ID": "2100000123456"}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_stable_adrule_id("123456")

        self.assertEqual(result, "2100000123456")
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["target"], "admrul")
        self.assertEqual(called_params["ID"], "123456")

    @patch("scripts.core.lawgokr_stable_id.requests.get")
    def test_returns_none_on_missing_field(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"행정규칙기본정보": {}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        self.assertIsNone(fetch_stable_adrule_id("123456"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_lawgokr_stable_id -v`
Expected: `ModuleNotFoundError: No module named 'scripts.core.lawgokr_stable_id'`

- [ ] **Step 3: 구현 작성**

`scripts/core/lawgokr_stable_id.py`:
```python
"""
law.go.kr 상세 API로 법령/행정규칙의 안정 ID(법령ID/행정규칙ID)를 조회한다.

같은 법령·행정규칙이라도 개정될 때마다 새 doc_id(MST/일련번호)가 발급되므로,
버전을 그룹핑하려면 이 안정 ID가 필요하다.
"""

from typing import Optional

import requests

from scripts.utils.logger_config import get_logger

logger = get_logger(__name__, scraper_type='lawgokr_stable_id')

API_BASE = "https://www.law.go.kr/DRF/lawService.do"
OC = "inwoong100"
TIMEOUT_SECONDS = 10


def fetch_stable_law_id(doc_id: str) -> Optional[str]:
    """MST(doc_id)로 법령ID(안정 ID)를 조회한다. 실패 시 None."""
    params = {"OC": OC, "target": "law", "MST": doc_id, "type": "JSON"}
    try:
        resp = requests.get(API_BASE, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        law_id = data.get("기본정보", {}).get("법령ID")
        return str(law_id) if law_id else None
    except Exception as e:
        logger.warning(f"법령ID 조회 실패 (MST={doc_id}): {e}")
        return None


def fetch_stable_adrule_id(doc_id: str) -> Optional[str]:
    """ID(doc_id, 행정규칙일련번호)로 행정규칙ID(안정 ID)를 조회한다. 실패 시 None."""
    params = {"OC": OC, "target": "admrul", "ID": doc_id, "type": "JSON"}
    try:
        resp = requests.get(API_BASE, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        adrule_id = data.get("행정규칙기본정보", {}).get("행정규칙ID")
        return str(adrule_id) if adrule_id else None
    except Exception as e:
        logger.warning(f"행정규칙ID 조회 실패 (ID={doc_id}): {e}")
        return None
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_lawgokr_stable_id -v`
Expected: `Ran 5 tests ... OK`

- [ ] **Step 5: 커밋**

```bash
git add scripts/core/lawgokr_stable_id.py tests/test_lawgokr_stable_id.py
git commit -m "feat: law.go.kr 안정 ID(법령ID/행정규칙ID) 조회 헬퍼 추가"
```

---

## Task 2: ArticleDiffBuilder (조문 단위 신구대조)

**Files:**
- Modify: `scripts/core/database/change_detector.py:12` (import), 파일 끝(238번째 줄 뒤)에 클래스 추가
- Test: `tests/test_article_diff_builder.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_article_diff_builder.py`:
```python
import unittest

from scripts.core.database.change_detector import ArticleDiffBuilder


class ArticleDiffBuilderTest(unittest.TestCase):
    def test_detects_revision_when_content_differs(self):
        old_articles = [{"article_number": "1", "content": "구 조문"}]
        new_articles = [{"article_number": "1", "content": "신 조문"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "1")
        self.assertEqual(diffs[0]["change_type"], "개정")
        self.assertEqual(diffs[0]["old_content"], "구 조문")
        self.assertEqual(diffs[0]["new_content"], "신 조문")

    def test_detects_new_article(self):
        old_articles = [{"article_number": "1", "content": "조문1"}]
        new_articles = [
            {"article_number": "1", "content": "조문1"},
            {"article_number": "1.1", "content": "신설 조문"},
        ]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "1.1")
        self.assertEqual(diffs[0]["change_type"], "신설")
        self.assertIsNone(diffs[0]["old_content"])

    def test_detects_deletion_via_removed_article(self):
        old_articles = [
            {"article_number": "1", "content": "조문1"},
            {"article_number": "2", "content": "조문2"},
        ]
        new_articles = [{"article_number": "1", "content": "조문1"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["article_number"], "2")
        self.assertEqual(diffs[0]["change_type"], "삭제")
        self.assertIsNone(diffs[0]["new_content"])

    def test_detects_deletion_via_placeholder_content(self):
        old_articles = [{"article_number": "3", "content": "조문3 원문"}]
        new_articles = [{"article_number": "3", "content": "<삭제>"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["change_type"], "삭제")

    def test_excludes_unchanged_articles(self):
        old_articles = [{"article_number": "1", "content": "동일 내용"}]
        new_articles = [{"article_number": "1", "content": "동일 내용"}]

        diffs = ArticleDiffBuilder.build(old_articles, new_articles)

        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_article_diff_builder -v`
Expected: `ImportError: cannot import name 'ArticleDiffBuilder'`

- [ ] **Step 3: import 라인 수정**

`scripts/core/database/change_detector.py:12` 수정:

Old:
```python
from typing import Dict, Optional, Tuple
```
New:
```python
from typing import Dict, List, Optional, Tuple
```

- [ ] **Step 4: 파일 끝에 ArticleDiffBuilder 클래스 추가**

`scripts/core/database/change_detector.py` 238번째 줄(파일 끝) 뒤에 추가:
```python


class ArticleDiffBuilder:
    """old/new 조문 리스트를 article_number 기준으로 매칭해 신구대조표를 만든다."""

    @staticmethod
    def build(old_articles: List[Dict], new_articles: List[Dict]) -> List[Dict]:
        """
        old_articles/new_articles: [{"article_number": str, "content": str}, ...]

        Returns:
            무변경 조문을 제외한 diff 리스트:
            [{"article_number": str, "change_type": "개정"|"신설"|"삭제",
              "old_content": str|None, "new_content": str|None}]
        """
        old_map = {
            a["article_number"]: a.get("content", "")
            for a in old_articles if a.get("article_number")
        }
        new_map = {
            a["article_number"]: a.get("content", "")
            for a in new_articles if a.get("article_number")
        }

        diffs = []
        for article_number in set(old_map) | set(new_map):
            old_content = old_map.get(article_number)
            new_content = new_map.get(article_number)

            if old_content is not None and new_content is not None:
                if old_content == new_content:
                    continue
                if ArticleDiffBuilder._is_deleted_marker(new_content):
                    diffs.append({
                        "article_number": article_number,
                        "change_type": "삭제",
                        "old_content": old_content,
                        "new_content": None,
                    })
                else:
                    diffs.append({
                        "article_number": article_number,
                        "change_type": "개정",
                        "old_content": old_content,
                        "new_content": new_content,
                    })
            elif old_content is None and new_content is not None:
                diffs.append({
                    "article_number": article_number,
                    "change_type": "신설",
                    "old_content": None,
                    "new_content": new_content,
                })
            elif old_content is not None and new_content is None:
                diffs.append({
                    "article_number": article_number,
                    "change_type": "삭제",
                    "old_content": old_content,
                    "new_content": None,
                })
        return diffs

    @staticmethod
    def _is_deleted_marker(content: str) -> bool:
        normalized = (content or "").strip().strip("<>").strip()
        return normalized == "삭제"
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_article_diff_builder -v`
Expected: `Ran 5 tests ... OK`

- [ ] **Step 6: 커밋**

```bash
git add scripts/core/database/change_detector.py tests/test_article_diff_builder.py
git commit -m "feat: 조문 단위 신구대조(ArticleDiffBuilder) 추가"
```

---

## Task 3: 그룹핑 + 승격 판단 순수 로직

**Files:**
- Create: `scripts/core/database/refresh_promotion.py`
- Test: `tests/test_refresh_promotion.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_refresh_promotion.py`:
```python
import unittest

from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)


class GroupActiveDocumentsTest(unittest.TestCase):
    def test_groups_by_stable_id_field_keeping_one_representative_per_doc_id(self):
        documents = [
            {"doc_id": "d1", "metadata": {"law_id": "G1", "effective": "2026-01-01",
                                           "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "d1", "metadata": {"law_id": "G1", "effective": "2026-01-01",
                                           "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "d2", "metadata": {"law_id": "G1", "effective": "2026-07-01",
                                           "is_upcoming": True, "created_at": "2026-06-01T00:00:00"}},
            {"doc_id": "d3", "metadata": {}},
        ]

        groups = group_active_documents_by_stable_id(documents, "law_id")

        self.assertEqual(set(groups.keys()), {"G1"})
        self.assertEqual(set(groups["G1"].keys()), {"d1", "d2"})
        self.assertEqual(groups["G1"]["d1"]["effective"], "2026-01-01")


class PlanGroupTransitionsTest(unittest.TestCase):
    def test_promotes_when_upcoming_becomes_current(self):
        doc_reps = {
            "old1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
            "new1": {"effective": "2026-07-01", "is_upcoming": True, "created_at": "2026-06-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "new1")
        self.assertEqual(plan["old_current"], "old1")
        self.assertTrue(plan["promoted"])
        self.assertEqual(plan["activate_current"], "new1")
        self.assertEqual(plan["deactivate"], ["old1"])
        self.assertEqual(plan["keep_upcoming"], [])

    def test_no_promotion_when_current_already_latest(self):
        doc_reps = {
            "cur1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "cur1")
        self.assertEqual(plan["old_current"], "cur1")
        self.assertFalse(plan["promoted"])
        self.assertIsNone(plan["activate_current"])
        self.assertEqual(plan["deactivate"], [])

    def test_keeps_only_latest_duplicate_upcoming_effective_date(self):
        doc_reps = {
            "cur1": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
            "up_old": {"effective": "2026-09-01", "is_upcoming": True, "created_at": "2026-06-01T00:00:00"},
            "up_new": {"effective": "2026-09-01", "is_upcoming": True, "created_at": "2026-07-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertFalse(plan["promoted"])
        self.assertEqual(plan["keep_upcoming"], ["up_new"])
        self.assertEqual(plan["deactivate"], ["up_old"])

    def test_deactivates_superseded_past_versions(self):
        doc_reps = {
            "cur_new": {"effective": "2026-07-01", "is_upcoming": False, "created_at": "2026-06-01T00:00:00"},
            "cur_stale": {"effective": "2026-01-01", "is_upcoming": False, "created_at": "2026-01-01T00:00:00"},
        }

        plan = plan_group_transitions(doc_reps, today="2026-07-21")

        self.assertEqual(plan["new_current"], "cur_new")
        self.assertEqual(plan["deactivate"], ["cur_stale"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_refresh_promotion -v`
Expected: `ModuleNotFoundError: No module named 'scripts.core.database.refresh_promotion'`

- [ ] **Step 3: 구현 작성**

`scripts/core/database/refresh_promotion.py`:
```python
"""
law/adrule 일일 재계산(refresh)에 필요한 순수 로직.

MongoDB I/O 없이 문서 메타데이터만으로 그룹핑/승격 판단을 수행한다.
실제 DB 반영은 scripts/core/flows/refresh_current_status_flow.py가 담당한다.
"""

from typing import Dict, List


def group_active_documents_by_stable_id(documents: List[Dict], id_field: str) -> Dict[str, Dict[str, Dict]]:
    """
    is_active=True 문서 목록을 metadata.<id_field> 기준으로 그룹핑한다.

    Returns:
        {group_id: {doc_id: {"effective": str, "is_upcoming": bool, "created_at": str}}}
    """
    groups: Dict[str, Dict[str, Dict]] = {}
    for doc in documents:
        doc_id = doc.get("doc_id")
        metadata = doc.get("metadata") or {}
        group_id = metadata.get(id_field)
        if not doc_id or not group_id:
            continue
        group = groups.setdefault(group_id, {})
        group.setdefault(doc_id, {
            "effective": metadata.get("effective") or "",
            "is_upcoming": bool(metadata.get("is_upcoming", False)),
            "created_at": metadata.get("created_at") or "",
        })
    return groups


def plan_group_transitions(doc_reps: Dict[str, Dict], today: str) -> Dict:
    """
    한 그룹(같은 law_id/adrule_id) 안에서 어떤 doc_id를 현재 버전으로 승격하고
    어떤 doc_id를 비활성화할지 결정한다.

    doc_reps: {doc_id: {"effective": str, "is_upcoming": bool, "created_at": str}}
    today: "YYYY-MM-DD"
    """
    current_candidates = {
        doc_id: rep for doc_id, rep in doc_reps.items()
        if rep.get("effective") and rep["effective"] <= today
    }
    upcoming_candidates = {
        doc_id: rep for doc_id, rep in doc_reps.items()
        if doc_id not in current_candidates
    }

    new_current = None
    if current_candidates:
        new_current = max(
            current_candidates,
            key=lambda doc_id: (
                current_candidates[doc_id]["effective"],
                current_candidates[doc_id].get("created_at", ""),
            ),
        )

    old_current = next(
        (doc_id for doc_id, rep in doc_reps.items() if rep.get("is_upcoming") is False),
        None,
    )

    promoted = bool(new_current) and new_current != old_current

    deactivate = set()
    for doc_id in current_candidates:
        if doc_id != new_current:
            deactivate.add(doc_id)
    if promoted and old_current and old_current != new_current:
        deactivate.add(old_current)

    by_effective: Dict[str, List[str]] = {}
    for doc_id, rep in upcoming_candidates.items():
        by_effective.setdefault(rep.get("effective", ""), []).append(doc_id)

    keep_upcoming = set()
    for effective, doc_ids in by_effective.items():
        if len(doc_ids) == 1:
            keep_upcoming.add(doc_ids[0])
            continue
        latest = max(doc_ids, key=lambda d: upcoming_candidates[d].get("created_at", ""))
        keep_upcoming.add(latest)
        for doc_id in doc_ids:
            if doc_id != latest:
                deactivate.add(doc_id)

    return {
        "new_current": new_current,
        "old_current": old_current,
        "promoted": promoted,
        "activate_current": new_current if promoted else None,
        "deactivate": sorted(deactivate),
        "keep_upcoming": sorted(keep_upcoming),
    }
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_refresh_promotion -v`
Expected: `Ran 5 tests ... OK`

- [ ] **Step 5: 커밋**

```bash
git add scripts/core/database/refresh_promotion.py tests/test_refresh_promotion.py
git commit -m "feat: law/adrule 그룹핑+승격 판단 순수 로직 추가"
```

---

## Task 4: 일일 재계산 Prefect Flow

**Files:**
- Create: `scripts/core/flows/refresh_current_status_flow.py`
- Test: `tests/test_refresh_current_status_flow.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_refresh_current_status_flow.py`:
```python
import unittest


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.update_one_calls = []
        self.update_many_calls = []

    def _get(self, doc, dotted_key):
        value = doc
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def find(self, query=None, projection=None):
        query = query or {}
        results = []
        for doc in self.documents:
            matched = True
            for key, condition in query.items():
                value = self._get(doc, key)
                if isinstance(condition, dict) and "$in" in condition:
                    if value not in condition["$in"]:
                        matched = False
                        break
                elif value != condition:
                    matched = False
                    break
            if matched:
                results.append(doc)
        return results

    def update_many(self, query, update):
        self.update_many_calls.append((query, update))

    def update_one(self, query, update):
        self.update_one_calls.append((query, update))


class ApplyPromotionTest(unittest.TestCase):
    def test_promoted_group_deactivates_old_and_activates_new_with_diff(self):
        from scripts.core.flows.refresh_current_status_flow import apply_promotion

        documents = [
            {"doc_id": "old1", "article_number": "1", "content": "구 조문1"},
            {"doc_id": "old1", "article_number": "2", "content": "구 조문2"},
            {"doc_id": "new1", "article_number": "1", "content": "구 조문1"},
            {"doc_id": "new1", "article_number": "2", "content": "신 조문2"},
            {"doc_id": "new1", "article_number": "3", "content": "신설 조문3"},
        ]
        collection = FakeCollection(documents)
        plan = {
            "new_current": "new1",
            "old_current": "old1",
            "promoted": True,
            "activate_current": "new1",
            "deactivate": ["old1"],
            "keep_upcoming": [],
        }

        apply_promotion(collection, "law-group-1", plan)

        self.assertEqual(len(collection.update_many_calls), 2)

        deactivate_call = collection.update_many_calls[0]
        self.assertEqual(deactivate_call[0], {"doc_id": {"$in": ["old1"]}, "metadata.is_active": True})
        self.assertEqual(deactivate_call[1]["$set"]["metadata.is_active"], False)
        self.assertIn("metadata.updated_at", deactivate_call[1]["$set"])

        activate_call = collection.update_many_calls[1]
        self.assertEqual(activate_call[0], {"doc_id": "new1"})
        self.assertEqual(activate_call[1]["$set"]["metadata.is_active"], True)
        self.assertEqual(activate_call[1]["$set"]["metadata.is_upcoming"], False)

        updated_articles = {call[0]["article_number"] for call in collection.update_one_calls}
        self.assertEqual(updated_articles, {"2", "3"})

    def test_not_promoted_makes_no_writes(self):
        from scripts.core.flows.refresh_current_status_flow import apply_promotion

        collection = FakeCollection([])
        plan = {
            "new_current": "cur1",
            "old_current": "cur1",
            "promoted": False,
            "activate_current": None,
            "deactivate": [],
            "keep_upcoming": [],
        }

        apply_promotion(collection, "law-group-1", plan)

        self.assertEqual(collection.update_many_calls, [])
        self.assertEqual(collection.update_one_calls, [])


class RefreshCollectionEndToEndTest(unittest.TestCase):
    def test_refresh_collection_promotes_group_end_to_end(self):
        from scripts.core.flows.refresh_current_status_flow import refresh_collection

        documents = [
            {"doc_id": "old1", "article_number": "1", "content": "구 조문",
             "metadata": {"is_active": True, "law_id": "G1", "effective": "2026-01-01",
                          "is_upcoming": False, "created_at": "2026-01-01T00:00:00"}},
            {"doc_id": "new1", "article_number": "1", "content": "신 조문",
             "metadata": {"is_active": True, "law_id": "G1", "effective": "2026-07-01",
                          "is_upcoming": True, "created_at": "2026-06-01T00:00:00"}},
        ]
        collection = FakeCollection(documents)

        summary = refresh_collection(collection, "law_id", today="2026-07-21")

        self.assertEqual(summary["groups_checked"], 1)
        self.assertEqual(summary["promoted"], 1)

        activate_calls = [c for c in collection.update_many_calls if c[0] == {"doc_id": "new1"}]
        self.assertEqual(len(activate_calls), 1)
        self.assertTrue(activate_calls[0][1]["$set"]["metadata.is_active"])
        self.assertFalse(activate_calls[0][1]["$set"]["metadata.is_upcoming"])

        updated_articles = {call[0]["article_number"] for call in collection.update_one_calls}
        self.assertEqual(updated_articles, {"1"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_refresh_current_status_flow -v`
Expected: `ModuleNotFoundError: No module named 'scripts.core.flows.refresh_current_status_flow'`

- [ ] **Step 3: 구현 작성**

`scripts/core/flows/refresh_current_status_flow.py`:
```python
"""
law/adrule의 '현재 시행 버전' 매일 재계산 Prefect flow.

lawService.do로 얻은 안정 ID(law_id/adrule_id)를 기준으로 그룹핑하여,
시행예정이던 버전이 오늘 날짜로 현행이 되면 승격시키고 구 버전을 비활성화한다.
크롤러(law/adrule scraper.py)는 더 이상 이 승격/비활성화를 직접 하지 않는다.
"""

import os
import sys
from datetime import date, datetime
from typing import Dict, Optional

from prefect import flow, get_run_logger

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.database.change_detector import ArticleDiffBuilder
from scripts.core.database.refresh_promotion import (
    group_active_documents_by_stable_id,
    plan_group_transitions,
)

logger = get_logger(__name__, scraper_type='refresh_current_status')


def apply_promotion(collection, group_id: str, plan: Dict) -> None:
    """plan_group_transitions()의 결정을 실제 MongoDB에 반영한다."""
    if plan["deactivate"]:
        collection.update_many(
            {"doc_id": {"$in": plan["deactivate"]}, "metadata.is_active": True},
            {"$set": {"metadata.is_active": False, "metadata.updated_at": datetime.now().isoformat()}},
        )

    if plan["promoted"] and plan["activate_current"]:
        new_current = plan["activate_current"]
        collection.update_many(
            {"doc_id": new_current},
            {"$set": {"metadata.is_active": True, "metadata.is_upcoming": False,
                      "metadata.updated_at": datetime.now().isoformat()}},
        )

        if plan["old_current"]:
            old_articles = list(collection.find(
                {"doc_id": plan["old_current"]}, {"article_number": 1, "content": 1}
            ))
            new_articles = list(collection.find(
                {"doc_id": new_current}, {"article_number": 1, "content": 1}
            ))
            diffs = ArticleDiffBuilder.build(old_articles, new_articles)
            new_by_article = {a["article_number"]: a for a in new_articles}
            for diff in diffs:
                if diff["article_number"] in new_by_article:
                    collection.update_one(
                        {"doc_id": new_current, "article_number": diff["article_number"]},
                        {"$set": {"metadata.update_summary": diff}},
                    )
            logger.info(f"[{group_id}] 승격: {plan['old_current']} → {new_current}, 조문 변경 {len(diffs)}건")


def refresh_collection(collection, id_field: str, today: Optional[str] = None) -> Dict:
    """단일 컬렉션(law 또는 adrule)의 모든 그룹을 재계산한다."""
    today = today or date.today().isoformat()
    active_docs = list(collection.find(
        {"metadata.is_active": True},
        {"doc_id": 1, f"metadata.{id_field}": 1, "metadata.effective": 1,
         "metadata.is_upcoming": 1, "metadata.created_at": 1},
    ))
    groups = group_active_documents_by_stable_id(active_docs, id_field)

    summary = {"groups_checked": 0, "promoted": 0, "deactivated": 0}
    for group_id, doc_reps in groups.items():
        plan = plan_group_transitions(doc_reps, today)
        apply_promotion(collection, group_id, plan)
        summary["groups_checked"] += 1
        if plan["promoted"]:
            summary["promoted"] += 1
        summary["deactivated"] += len(plan["deactivate"])
    return summary


@flow(name="refresh-current-status", description="law/adrule 현재 시행 버전 일일 재계산")
def refresh_current_status_flow() -> Dict:
    from scripts.core.database.mongo_client import get_mongo_db

    run_logger = get_run_logger()
    db = get_mongo_db()
    today = date.today().isoformat()

    law_summary = refresh_collection(db["law"], "law_id", today)
    run_logger.info(f"law 재계산 완료: {law_summary}")

    adrule_summary = refresh_collection(db["adrule"], "adrule_id", today)
    run_logger.info(f"adrule 재계산 완료: {adrule_summary}")

    return {
        "law": law_summary,
        "adrule": adrule_summary,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    result = refresh_current_status_flow()
    print(f"\n최종 결과: {result}")
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_refresh_current_status_flow -v`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: 커밋**

```bash
git add scripts/core/flows/refresh_current_status_flow.py tests/test_refresh_current_status_flow.py
git commit -m "feat: law/adrule 현재 시행 버전 일일 재계산 flow 추가"
```

---

## Task 5: deploy.py에 일일 refresh 배포 추가

**Files:**
- Modify: `deploy.py`

**Files:** DB/네트워크 없이 순수 로직만 다루는 앞의 태스크들과 달리, 이 태스크는 CLI 배선(argparse + subprocess 호출)만 바꾸는 작업이라 실제 Prefect 서버 없이는 동작 검증이 불가능하다. 기존 `deploy_scraper()`도 별도 유닛 테스트가 없다 — 동일한 컨벤션을 따라 코드 수정 후 `python3 -c "import deploy"` 임포트 스모크 테스트로 문법 오류만 확인한다.

- [ ] **Step 1: `--refresh` 플래그 추가**

`deploy.py`에서 `parse_args()` 함수 내부:

Old:
```python
    parser.add_argument(
        '--schedule',
        default='0 9 * * 1',  # 매주 월요일 09:00
        help='Cron 스케줄 (기본값: 매주 월요일 09:00)'
    )
    
    return parser.parse_args()
```
New:
```python
    parser.add_argument(
        '--schedule',
        default='0 9 * * 1',  # 매주 월요일 09:00
        help='Cron 스케줄 (기본값: 매주 월요일 09:00)'
    )

    parser.add_argument(
        '--refresh',
        action='store_true',
        help='law/adrule 현재 시행 버전 일일 재계산 deployment 생성'
    )

    return parser.parse_args()
```

- [ ] **Step 2: `deploy_refresh_flow()` 함수 추가**

`deploy.py`에서 `deploy_scraper()` 함수 바로 뒤, `list_deployments()` 함수 앞:

Old:
```python
    cmd = f"cd {project_root} && python3 {temp_file}"
    return run_command(cmd, f"{config.display_name} deployment 생성")


def list_deployments():
```
New:
```python
    cmd = f"cd {project_root} && python3 {temp_file}"
    return run_command(cmd, f"{config.display_name} deployment 생성")


def deploy_refresh_flow(schedule='10 0 * * *'):
    """law/adrule 현재 시행 버전 일일 재계산 deployment 생성"""
    deploy_script = f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from prefect.deployments import Deployment
from scripts.core.flows.refresh_current_status_flow import refresh_current_status_flow

deployment = Deployment.build_from_flow(
    flow=refresh_current_status_flow,
    name="refresh-current-status",
    schedule="{schedule}"
)

deployment.apply()
print(f"✅ {{deployment.name}} 배포 완료")
"""
    temp_file = "/tmp/deploy_refresh_current_status.py"
    with open(temp_file, 'w') as f:
        f.write(deploy_script)

    cmd = f"cd {project_root} && python3 {temp_file}"
    return run_command(cmd, "law/adrule 현재 시행 버전 재계산 deployment 생성")


def list_deployments():
```

- [ ] **Step 3: `main()`에 `--refresh` 분기 추가**

`deploy.py`의 `main()` 함수 내부:

Old:
```python
    # --delete 옵션
    if args.delete:
        print(f"\n🗑️  다음 deployment를 삭제합니다: {', '.join(args.delete)}")
        for scraper_type in args.delete:
            delete_deployment(scraper_type)
        return 0
    
    # 배포할 scraper 결정
```
New:
```python
    # --delete 옵션
    if args.delete:
        print(f"\n🗑️  다음 deployment를 삭제합니다: {', '.join(args.delete)}")
        for scraper_type in args.delete:
            delete_deployment(scraper_type)
        return 0

    # --refresh 옵션 (law/adrule 현재 시행 버전 일일 재계산)
    if args.refresh:
        print("\n🔍 Prefect 서버 상태 확인 중...")
        result = subprocess.run("prefect server health-check", shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("\n⚠️  Prefect 서버가 실행 중이지 않습니다.")
            print("   다음 명령어로 서버를 시작하세요:")
            print("   prefect server start")
            print()
            return 1
        print("✅ Prefect 서버가 실행 중입니다.\n")
        success = deploy_refresh_flow()
        return 0 if success else 1

    # 배포할 scraper 결정
```

- [ ] **Step 4: 임포트 스모크 테스트**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -c "import deploy; print('ok')"`
Expected: `ok`

- [ ] **Step 5: 커밋**

```bash
git add deploy.py
git commit -m "feat: deploy.py에 law/adrule 일일 refresh flow 배포 옵션(--refresh) 추가"
```

---

## Task 6: law/logic/scraper.py — 안정 ID 조회로 교체, Step-0 승격 블록 삭제

**Files:**
- Modify: `scripts/law/logic/scraper.py:16-18` (import), `scripts/law/logic/scraper.py:269-312` (save_to_mongodb 앞부분), `scripts/law/logic/scraper.py:343-345` (article_doc law_id 대입부)

이 태스크는 실제 MongoDB/Playwright에 의존하는 코드라 이 저장소의 다른 스크래퍼들과 마찬가지로 유닛 테스트 대상이 아니다 (기존에도 `scripts/law/logic/scraper.py`에는 테스트가 없다). 문법 오류는 임포트 스모크 테스트로, 실제 동작은 Step 5의 통합 테스트로 검증한다.

- [ ] **Step 1: import 추가**

`scripts/law/logic/scraper.py:16-18`

Old:
```python
from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import article_chunk_id
```
New:
```python
from scripts.utils.logger_config import get_logger
from scripts.core.database.unified_repository import UnifiedDocumentRepository
from scripts.core.identifiers import article_chunk_id
from scripts.core.lawgokr_stable_id import fetch_stable_law_id
```

- [ ] **Step 2: Step-0 승격 블록 삭제, 안정 ID 조회로 교체**

`scripts/law/logic/scraper.py:269-297`

Old:
```python
    try:
        if not unified_doc:
            logger.error("저장할 문서가 없습니다.")
            return False
        
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db)
        
        # 0. 같은 law_id의 구 현행 버전 비활성화
        #    현재 크롤링 중인 법령이 '현행' 버전일 때만 실행
        #    (시행예정 버전은 구 버전을 비활성화하지 않음)
        law_id = unified_doc.get("law_id", "")
        is_upcoming = unified_doc.get("is_upcoming", False)
        current_doc_id = unified_doc.get("doc_id", "")
        if law_id and not is_upcoming:
            col = db["law"]
            col.update_many(
                {
                    "metadata.law_id": law_id,
                    "metadata.is_active": True,
                    "metadata.is_upcoming": False,
                    "doc_id": {"$ne": current_doc_id},
                },
                {"$set": {"metadata.is_active": False}},
            )

        # 1. 조별로 분리
        articles = split_articles_by_number(unified_doc["content"])
```
New:
```python
    try:
        if not unified_doc:
            logger.error("저장할 문서가 없습니다.")
            return False
        
        from scripts.core.database.mongo_client import get_mongo_db
        db = get_mongo_db()
        repo = UnifiedDocumentRepository(db)

        # 구 현행 버전 비활성화/승격은 refresh_current_status_flow(일일 배치)가 전담한다.
        # 여기서는 법령ID(안정 ID)만 조회해서 저장해둔다.
        base_doc_id = unified_doc["doc_id"]
        law_id = unified_doc.get("law_id", "")
        stable_law_id = fetch_stable_law_id(base_doc_id)
        if stable_law_id:
            law_id = stable_law_id
        elif law_id:
            logger.warning(f"법령ID API 조회 실패, title 기반 law_id로 대체: doc_id={base_doc_id}")

        # 1. 조별로 분리
        articles = split_articles_by_number(unified_doc["content"])
```

- [ ] **Step 3: 중복된 base_doc_id 재선언 제거**

`scripts/law/logic/scraper.py` (Step 2 적용 후 기준 라인 번호가 밀리므로 아래 old_string으로 위치 특정)

Old:
```python
        # 2. 각 조마다 독립 문서 생성 및 저장
        base_doc_id = unified_doc["doc_id"]
        saved_count = 0
        failed_count = 0
        any_changed = False
```
New:
```python
        # 2. 각 조마다 독립 문서 생성 및 저장
        saved_count = 0
        failed_count = 0
        any_changed = False
```

- [ ] **Step 4: article_doc의 law_id 대입을 안정 ID(law_id 로컬 변수) 기준으로 교체**

Old:
```python
                # 법령ID (같은 법령의 다른 버전 추적용)
                if unified_doc.get("law_id"):
                    article_doc["metadata"]["law_id"] = unified_doc["law_id"]
```
New:
```python
                # 법령ID (같은 법령의 다른 버전 추적용, 안정 ID 우선)
                if law_id:
                    article_doc["metadata"]["law_id"] = law_id
```

- [ ] **Step 5: 임포트 스모크 테스트**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -c "from scripts.law.logic import scraper; print('ok')"`
Expected: `ok`

- [ ] **Step 6: 통합 테스트 (실제 크롤 1건)**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 crawl.py --scraper law --pages 1 --concurrent 1`
Expected: 에러 없이 완료. 로그에 `법령ID API 조회 실패` 경고가 없으면 정상(있어도 크롤 자체는 계속 진행되어야 함). MongoDB `law` 컬렉션에서 방금 크롤된 doc_id의 `metadata.law_id`가 6자리 숫자 형태(API 응답 형식)인지 확인.

- [ ] **Step 7: 커밋**

```bash
git add scripts/law/logic/scraper.py
git commit -m "refactor: law 스크래퍼 — 법령ID를 API로 조회하고 구버전 비활성화 책임을 refresh flow로 이관"
```

---

## Task 7: adrule/logic/list_scraper.py — is_upcoming 계산 추가

**Files:**
- Modify: `scripts/adrule/logic/list_scraper.py`
- Test: `tests/test_adrule_list_scraper.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_adrule_list_scraper.py`:
```python
import unittest
from unittest.mock import patch

from scripts.adrule.logic.list_scraper import _fetch_page


class IsUpcomingComputationTest(unittest.TestCase):
    @patch("scripts.adrule.logic.list_scraper._fetch_page")
    @patch("scripts.adrule.logic.list_scraper._total_pages")
    @patch("scripts.adrule.logic.list_scraper.date")
    def test_marks_future_effective_date_as_upcoming(self, mock_date, mock_total_pages, mock_fetch_page):
        import asyncio
        from scripts.adrule.logic.list_scraper import fetch_urls

        mock_date.today.return_value.strftime.return_value = "20260721"
        mock_total_pages.return_value = 1
        mock_fetch_page.return_value = [
            {"행정규칙일련번호": "111", "시행일자": "20260101", "행정규칙명": "과거 시행 규칙", "소관부처명": "고용노동부"},
            {"행정규칙일련번호": "222", "시행일자": "20261231", "행정규칙명": "미래 시행 규칙", "소관부처명": "고용노동부"},
        ]

        items = asyncio.run(fetch_urls("https://example.com", max_pages_arg=1))

        by_seq = {item["url"]: item for item in items}
        past_item = by_seq["https://www.law.go.kr/admRulInfoP.do?admRulSeq=111"]
        future_item = by_seq["https://www.law.go.kr/admRulInfoP.do?admRulSeq=222"]

        self.assertFalse(past_item["is_upcoming"])
        self.assertTrue(future_item["is_upcoming"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_adrule_list_scraper -v`
Expected: `KeyError: 'is_upcoming'` (또는 `AttributeError: <module> does not have the attribute 'date'`)

- [ ] **Step 3: `date` import 추가**

`scripts/adrule/logic/list_scraper.py:1-7`

Old:
```python
import math
import re
import time
import os
import sys

import requests
```
New:
```python
import math
import re
import time
import os
import sys
from datetime import date

import requests
```

- [ ] **Step 4: fetch_urls에 today 계산 + docstring 갱신**

Old:
```python
async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    행정규칙 목록 API를 순회하며 상세 페이지 URL과 시행일자를 반환합니다.

    Returns:
        list: [{"name": str, "url": str, "effective": str}, ...]
        - url: https://www.law.go.kr/admRulInfoP.do?admRulSeq={행정규칙일련번호}
        - effective: 시행일자 (YYYYMMDD)
    """
    logger.info(f"행정규칙 목록 API 수집 시작 (참조 URL: {start_url})")
```
New:
```python
async def fetch_urls(start_url: str, max_pages_arg: int | None = None):
    """
    행정규칙 목록 API를 순회하며 상세 페이지 URL과 시행일자를 반환합니다.

    Returns:
        list: [{"name": str, "url": str, "effective": str, "is_upcoming": bool}, ...]
        - url: https://www.law.go.kr/admRulInfoP.do?admRulSeq={행정규칙일련번호}
        - effective: 시행일자 (YYYYMMDD)
        - is_upcoming: 시행일자가 오늘보다 미래인지 여부 (law list_scraper와 동일 기준)
    """
    logger.info(f"행정규칙 목록 API 수집 시작 (참조 URL: {start_url})")
    today = date.today().strftime("%Y%m%d")
```

- [ ] **Step 5: urls_found 항목에 is_upcoming 추가**

Old:
```python
            safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() if name else f"adrule_{seq}"
            urls_found.append({
                "name": safe_name,
                "url": url,
                "effective": efy,  # 시행일자 — update 모드 변경 감지에 사용
            })
```
New:
```python
            safe_name = re.sub(r'[\\/*?:"<>|]', "", name).strip() if name else f"adrule_{seq}"
            urls_found.append({
                "name": safe_name,
                "url": url,
                "effective": efy,  # 시행일자 — update 모드 변경 감지에 사용
                "is_upcoming": bool(efy) and efy > today,
            })
```

- [ ] **Step 6: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_adrule_list_scraper -v`
Expected: `Ran 1 test ... OK`

- [ ] **Step 7: 커밋**

```bash
git add scripts/adrule/logic/list_scraper.py tests/test_adrule_list_scraper.py
git commit -m "feat: adrule list_scraper에 is_upcoming 계산 추가 (law와 동일 기준)"
```

---

## Task 8: adrule/logic/scraper.py — UnifiedDocumentRepository로 전환, adrule_id/is_upcoming 배선

**Files:**
- Modify: `scripts/adrule/logic/scraper.py:28` (import), `scripts/adrule/logic/scraper.py:398-399` (시그니처), `scripts/adrule/logic/scraper.py:429-431` (repo 초기화), `scripts/adrule/logic/scraper.py:473-517` (조문 저장 로직), `scripts/adrule/logic/scraper.py:534-554` (async 래퍼), `scripts/adrule/logic/scraper.py:558-561` (dict 파라미터 파싱), `scripts/adrule/logic/scraper.py:624-629` (doc_id 이후 adrule_id 조회), `scripts/adrule/logic/scraper.py:658-667` (호출부)

law.py와 마찬가지로 실제 Playwright/MongoDB에 의존하므로 유닛 테스트 대상이 아니다. 임포트 스모크 테스트 + 통합 크롤로 검증한다.

- [ ] **Step 1: import 추가**

Old:
```python
from scripts.core.identifiers import article_chunk_id, normalize_article_number
```
New:
```python
from scripts.core.identifiers import article_chunk_id, normalize_article_number
from scripts.core.lawgokr_stable_id import fetch_stable_adrule_id
```

- [ ] **Step 2: save_to_mongodb 시그니처에 is_upcoming/adrule_id 추가**

Old:
```python
def save_to_mongodb(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                   sub_title: str = "", effective_date: str = None, dept_name: str = None) -> str:
```
New:
```python
def save_to_mongodb(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                   sub_title: str = "", effective_date: str = None, dept_name: str = None,
                   is_upcoming: bool = False, adrule_id: str = None) -> str:
```

- [ ] **Step 3: raw collection 대신 UnifiedDocumentRepository 사용**

Old:
```python
            from scripts.core.database.mongo_client import get_mongo_db
            db = get_mongo_db()
            collection = db['adrule']
```
New:
```python
            from scripts.core.database.mongo_client import get_mongo_db
            db = get_mongo_db()
            repo = UnifiedDocumentRepository(db, collection_name="adrule")
```

- [ ] **Step 4: 조문 저장 로직을 upsert_with_change_detection 호출로 교체**

Old:
```python
                meta = {
                    "source_url": url,
                    "source_type": "web",
                    "effective": effective_date,
                    "updated_at": datetime.now().isoformat(),
                    "is_active": True,
                    "article_index": idx,
                    "total_articles": len(articles),
                }
                if dept_code:
                    meta["dept_code"] = dept_code
                if dept_name:
                    meta["dept_name"] = dept_name

                article_doc = {
                    "chunk_id": article_chunk_id("adrule", doc_id, article_number),
                    "doc_id": doc_id,
                    "article_number": article_number,
                    "article_title": article['article_title'],
                    "title": doc_title,
                    "sub_title": sub_title,
                    "content": article['content'],
                    "doc_type": "행정규칙",
                }
                article_doc = enrich_source_document(article_doc, "adrule")

                set_fields = dict(article_doc)
                set_fields.update({f"metadata.{k}": v for k, v in meta.items()})

                # MongoDB에 upsert (created_at은 최초 insert 시에만 설정)
                result = collection.update_one(
                    {"doc_id": doc_id, "article_number": article_number},
                    {
                        "$set": set_fields,
                        "$setOnInsert": {"metadata.created_at": datetime.now().strftime('%Y-%m-%d')},
                    },
                    upsert=True
                )

                if result.upserted_id:
                    logger.info(f"✅ 새 조문 생성: {doc_id} (article_number: {article_number})")
                else:
                    logger.info(f"🔄 조문 업데이트: {doc_id} (article_number: {article_number})")

                saved_count += 1
```
New:
```python
                meta = {
                    "source_url": url,
                    "source_type": "web",
                    "effective": effective_date,
                    "article_index": idx,
                    "total_articles": len(articles),
                    "is_upcoming": is_upcoming,
                }
                if dept_code:
                    meta["dept_code"] = dept_code
                if dept_name:
                    meta["dept_name"] = dept_name
                if adrule_id:
                    meta["adrule_id"] = adrule_id

                article_doc = {
                    "chunk_id": article_chunk_id("adrule", doc_id, article_number),
                    "doc_id": doc_id,
                    "article_number": article_number,
                    "article_title": article['article_title'],
                    "title": doc_title,
                    "sub_title": sub_title,
                    "content": article['content'],
                    "doc_type": "행정규칙",
                    "metadata": meta,
                }

                # 변경 감지 포함 upsert (law.py와 동일한 버전관리 경로)
                _, action_result = repo.upsert_with_change_detection(article_doc)

                if action_result["action"] == "insert":
                    logger.info(f"💾 신규 저장: {doc_id} (article_number: {article_number})")
                elif action_result["action"] == "new_version":
                    logger.info(f"✅ 새 버전 생성: {doc_id} (article_number: {article_number})")
                elif action_result["action"] == "update_existing":
                    logger.info(f"🔄 기존 버전 유지: {doc_id} (article_number: {article_number})")

                if action_result["action"] in ("insert", "new_version"):
                    any_changed = True
                saved_count += 1
```

- [ ] **Step 5: enrich_source_document import 정리**

`scripts.core.database.source_versioning`의 `enrich_source_document`는 이제 이 파일에서 직접 호출하지 않는다(`upsert_with_change_detection` 내부에서 처리). import 라인은 다른 곳에서 쓰이지 않으므로 제거한다.

Run: `grep -n "enrich_source_document" scripts/adrule/logic/scraper.py`
Expected: import 라인 1건만 남아있어야 함(본문 호출 없음) — 있다면 아래로 제거.

Old:
```python
from scripts.core.database.source_versioning import enrich_source_document
```
New: (라인 삭제 — 빈 문자열로 교체하지 말고 해당 줄 자체를 지운다)

- [ ] **Step 6: save_to_mongodb_async에 is_upcoming/adrule_id 배선**

Old:
```python
async def save_to_mongodb_async(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                                sub_title: str = "", effective_date: str = None, executor=None,
                                dept_name: str = None) -> str:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    import functools
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)

    try:
        fn = functools.partial(
            save_to_mongodb,
            chunks, doc_title, doc_id, url,
            dept_code, sub_title, effective_date, dept_name,
        )
        result = await loop.run_in_executor(executor, fn
        )
        return result
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False
```
New:
```python
async def save_to_mongodb_async(chunks: list, doc_title: str, doc_id: str, url: str, dept_code: str = None,
                                sub_title: str = "", effective_date: str = None, executor=None,
                                dept_name: str = None, is_upcoming: bool = False,
                                adrule_id: str = None) -> str:
    """MongoDB 저장을 async로 처리 (event loop blocking 방지)"""
    import functools
    loop = asyncio.get_event_loop()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)

    try:
        fn = functools.partial(
            save_to_mongodb,
            chunks, doc_title, doc_id, url,
            dept_code=dept_code, sub_title=sub_title, effective_date=effective_date,
            dept_name=dept_name, is_upcoming=is_upcoming, adrule_id=adrule_id,
        )
        result = await loop.run_in_executor(executor, fn)
        return result
    except Exception as e:
        logger.error(f"MongoDB 비동기 저장 실패: {e}")
        return False
```

- [ ] **Step 7: scrape_and_save — url dict에서 is_upcoming 파싱**

Old:
```python
    dept_name = None
    if isinstance(url, dict):
        dept_name = url.get("dept_name", "")
        url = url.get("url", "")
```
New:
```python
    dept_name = None
    is_upcoming = False
    if isinstance(url, dict):
        dept_name = url.get("dept_name", "")
        is_upcoming = bool(url.get("is_upcoming", False))
        url = url.get("url", "")
```

- [ ] **Step 8: scrape_and_save — doc_id 확정 후 adrule_id 조회**

Old:
```python
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            doc_id_keys = ['admRulSeq', 'admRulId']
            doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
            if not doc_id:
                doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)
```
New:
```python
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            doc_id_keys = ['admRulSeq', 'admRulId']
            doc_id = next((query_params.get(key, [None])[0] for key in doc_id_keys if query_params.get(key)), None)
            if not doc_id:
                doc_id = re.sub(r'[^a-zA-Z0-9]', '', doc_title)

            # 행정규칙ID(안정 ID) 조회 — 실패해도 크롤링은 계속 진행
            adrule_id = fetch_stable_adrule_id(doc_id)
            if not adrule_id:
                logger.warning(f"행정규칙ID API 조회 실패: doc_id={doc_id}")
```

- [ ] **Step 9: save_to_mongodb_async 호출부에 is_upcoming/adrule_id 전달**

Old:
```python
                _adrule_action = await save_to_mongodb_async(
                    chunks,
                    doc_title,
                    doc_id,
                    url,
                    dept_code,
                    sub_title=sub_title,
                    effective_date=effective_date,
                    dept_name=dept_name,
                )
```
New:
```python
                _adrule_action = await save_to_mongodb_async(
                    chunks,
                    doc_title,
                    doc_id,
                    url,
                    dept_code,
                    sub_title=sub_title,
                    effective_date=effective_date,
                    dept_name=dept_name,
                    is_upcoming=is_upcoming,
                    adrule_id=adrule_id,
                )
```

- [ ] **Step 10: 임포트 스모크 테스트**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -c "from scripts.adrule.logic import scraper; print('ok')"`
Expected: `ok`

- [ ] **Step 11: 통합 테스트 (실제 크롤 1건)**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 crawl.py --scraper adrule --pages 1 --concurrent 1`
Expected: 에러 없이 완료. MongoDB `adrule` 컬렉션에서 방금 크롤된 조문 문서의 `metadata.adrule_id`가 채워져 있고, 같은 doc_id+article_number로 재실행 시 `action: update_existing`(내용 동일) 로그가 찍히는지 확인 — 기존의 무조건 update_one 방식과 달리 이제 변경 감지가 실제로 동작해야 한다.

- [ ] **Step 12: 커밋**

```bash
git add scripts/adrule/logic/scraper.py
git commit -m "refactor: adrule 스크래퍼를 UnifiedDocumentRepository 기반 버전관리로 전환, adrule_id/is_upcoming 배선"
```

---

## Task 9: 안정 ID Backfill 스크립트 (1회성)

**Files:**
- Create: `scripts/migrations/backfill_stable_ids.py`
- Test: `tests/test_backfill_stable_ids.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backfill_stable_ids.py`:
```python
import unittest

from scripts.migrations.backfill_stable_ids import plan_backfill


class PlanBackfillTest(unittest.TestCase):
    def test_splits_successful_lookups_from_failures(self):
        def fake_lookup(doc_id):
            return {"d1": "S1", "d2": "S2"}.get(doc_id)

        updates, failures = plan_backfill(["d1", "d2", "d3"], fake_lookup)

        self.assertEqual(updates, [("d1", "S1"), ("d2", "S2")])
        self.assertEqual(failures, ["d3"])

    def test_empty_doc_ids_produces_empty_results(self):
        updates, failures = plan_backfill([], lambda doc_id: "unused")

        self.assertEqual(updates, [])
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_backfill_stable_ids -v`
Expected: `ModuleNotFoundError: No module named 'scripts.migrations.backfill_stable_ids'`

- [ ] **Step 3: 구현 작성**

`scripts/migrations/backfill_stable_ids.py`:
```python
#!/usr/bin/env python3
"""
law/adrule의 metadata.law_id / metadata.adrule_id를 안정 ID로 정규화하는 1회성 backfill.

기존에는 list_scraper가 넘긴 title 텍스트를 law_id로 써왔기 때문에(같은 법령이라도
띄어쓰기·개정 등으로 값이 흔들릴 수 있음), law.go.kr 상세 API로 조회한 진짜 안정 ID로
전체 문서를 다시 채운다.

사용법:
    python3 scripts/migrations/backfill_stable_ids.py                # dry-run (기본값)
    python3 scripts/migrations/backfill_stable_ids.py --execute       # 실제 DB 갱신
    python3 scripts/migrations/backfill_stable_ids.py --collections law
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.utils.logger_config import get_logger
from scripts.core.lawgokr_stable_id import fetch_stable_law_id, fetch_stable_adrule_id

logger = get_logger(__name__, scraper_type='backfill_stable_ids')

RATE_LIMIT_DELAY_SECONDS = 0.3

COLLECTIONS = {
    "law": ("law_id", fetch_stable_law_id),
    "adrule": ("adrule_id", fetch_stable_adrule_id),
}


def plan_backfill(doc_ids: List[str], lookup_fn: Callable[[str], Optional[str]]) -> Tuple[List[Tuple[str, str]], List[str]]:
    """doc_id별로 안정 ID를 조회해 (doc_id, stable_id) 갱신목록과 실패 doc_id 목록을 반환한다."""
    updates: List[Tuple[str, str]] = []
    failures: List[str] = []
    for doc_id in doc_ids:
        stable_id = lookup_fn(doc_id)
        if stable_id:
            updates.append((doc_id, stable_id))
        else:
            failures.append(doc_id)
    return updates, failures


def run_backfill(collection_name: str, dry_run: bool = True) -> Dict:
    from scripts.core.database.mongo_client import get_mongo_db

    id_field, lookup_fn = COLLECTIONS[collection_name]

    def rate_limited_lookup(doc_id: str) -> Optional[str]:
        result = lookup_fn(doc_id)
        time.sleep(RATE_LIMIT_DELAY_SECONDS)
        return result

    db = get_mongo_db()
    collection = db[collection_name]
    doc_ids = collection.distinct("doc_id")
    logger.info(f"[{collection_name}] distinct doc_id {len(doc_ids)}개 발견")

    updates, failures = plan_backfill(doc_ids, rate_limited_lookup)

    if dry_run:
        logger.info(f"[{collection_name}] --dry-run: {len(updates)}건 갱신 예정, {len(failures)}건 조회 실패")
        for doc_id, stable_id in updates[:20]:
            logger.info(f"  {doc_id} -> metadata.{id_field}={stable_id}")
    else:
        for doc_id, stable_id in updates:
            collection.update_many(
                {"doc_id": doc_id},
                {"$set": {f"metadata.{id_field}": stable_id}},
            )
        logger.info(f"[{collection_name}] {len(updates)}건 갱신 완료")

    if failures:
        logger.warning(f"[{collection_name}] 조회 실패 {len(failures)}건 (수동 확인 필요): {failures[:20]}")

    return {"collection": collection_name, "updated": len(updates), "failed": len(failures)}


def main():
    parser = argparse.ArgumentParser(description="law/adrule 안정 ID(law_id/adrule_id) backfill")
    parser.add_argument("--collections", nargs="+", choices=list(COLLECTIONS), default=list(COLLECTIONS))
    parser.add_argument("--execute", action="store_true", help="실제 DB 갱신 수행 (기본값: dry-run)")
    args = parser.parse_args()

    dry_run = not args.execute
    for collection_name in args.collections:
        run_backfill(collection_name, dry_run=dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest tests.test_backfill_stable_ids -v`
Expected: `Ran 2 tests ... OK`

- [ ] **Step 5: 커밋**

```bash
git add scripts/migrations/backfill_stable_ids.py tests/test_backfill_stable_ids.py
git commit -m "feat: law/adrule 안정 ID backfill 스크립트 추가"
```

---

## Task 10: 전체 회귀 테스트 + 운영 절차 확인

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 신규/기존 유닛 테스트 전체 실행**

Run:
```bash
cd /Users/woong/Documents/Roeum/roeum-crawler && python3 -m unittest discover -s tests -v
```
Expected: 기존 테스트(test_mongo_config, test_provenance_migration, test_source_versioning) + 이번에 추가한 6개 테스트 파일 모두 `OK`.

- [ ] **Step 2: 맥미니 배포 절차 정리 (실행은 사용자 확인 후)**

이 단계는 코드 변경이 아니라 운영 절차이므로, 구현 완료 후 사용자에게 아래 순서를 안내하고 진행 여부를 확인받는다:

1. 맥미니(`ssh loum@100.93.93.51`)에 이 브랜치를 pull.
2. `python3 scripts/migrations/backfill_stable_ids.py` (dry-run) 실행 → 갱신 예정 건수/실패 건수 확인.
3. 문제 없으면 `python3 scripts/migrations/backfill_stable_ids.py --execute` 실행.
4. `prefect server start` 상태에서 `python3 deploy.py --refresh` 실행 → `refresh-current-status` deployment 생성 확인.
5. 기존 `python3 deploy.py`(law/adrule 포함 전체 스크래퍼)도 재배포해 스크래퍼 코드 변경분(law_id/adrule_id 조회, UnifiedDocumentRepository 전환)이 반영되도록 한다.
6. 다음 날 정기 크론 실행 후 Discord 리포트로 law/adrule 정상 동작 확인.

---

## Self-Review

**스펙 커버리지 확인 (2026-07-17 스펙 대비):**
- law_id/adrule_id 안정 ID 조회 → Task 1, 6, 8
- Step-0 promotion 블록 제거, 승격 책임을 flow로 이관 → Task 6
- adrule의 is_upcoming 계산 + UnifiedDocumentRepository 전환 → Task 7, 8
- ArticleDiffBuilder (개정/신설/삭제/무변경 4분류, 조의2 서브번호, "삭제" 문구 케이스) → Task 2
- 일일 refresh flow의 a-f 승격 판단 알고리즘 → Task 3, 4
- backfill_stable_ids.py (--dry-run 기본값) → Task 9
- deploy.py `--refresh` 플래그 + 일일 스케줄 → Task 5
- law_id/adrule_id 조회 헬퍼의 404/필드 누락 처리 검증 → Task 1 (`test_returns_none_on_missing_field`, `test_returns_none_on_request_exception`)

**플레이스홀더 스캔:** "TBD", "TODO", "add appropriate error handling" 등 없음. 모든 스텝에 완전한 코드/명령어 포함.

**타입/시그니처 일관성 확인:**
- `plan_group_transitions(doc_reps, today)`의 반환 키(`new_current`, `old_current`, `promoted`, `activate_current`, `deactivate`, `keep_upcoming`) — Task 3에서 정의, Task 4의 `refresh_collection`/`apply_promotion`에서 동일하게 사용.
- `ArticleDiffBuilder.build(old_articles, new_articles)` — Task 2에서 정의, Task 4의 `apply_promotion`에서 동일 시그니처로 호출.
- `fetch_stable_law_id`/`fetch_stable_adrule_id` — Task 1에서 정의, Task 6/8/9에서 동일 함수명으로 import.
- `save_to_mongodb`/`save_to_mongodb_async`(adrule)의 `is_upcoming`/`adrule_id` 파라미터명이 Task 8의 Step 2, 6, 9에서 일관됨.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-law-adrule-version-tracking.md`. 두 실행 옵션:

**1. Subagent-Driven (recommended)** - 태스크마다 새 subagent를 붙여 실행, 태스크 사이마다 리뷰

**2. Inline Execution** - 이 세션에서 executing-plans로 배치 실행, 체크포인트마다 리뷰

어느 쪽으로 진행할까요?
