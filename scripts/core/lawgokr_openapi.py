"""국가법령정보 Open API(DRF) 공통 — 목록 검색·상세 조회·소관 법령 기준·범위 판정.

2026-11-15 law.go.kr 화면 개편으로 옛 목록 주소(lsAstSc.do·precAstScListR.do·detcAstScListR.do·
expcAstScListR.do·cgmExpcAstScListR.do)가 목록 대신 새 홈페이지를 돌려줘 5종 목록이 0건이 됐다.
목록은 화면 대신 Open API 로 받는다(행정규칙·행정심판재결례는 이미 Open API).

'고용노동부 소관 분류'는 Open API 에 없으므로 소관 법령 이름으로 범위를 정한다.
- 소관 법령: 고용노동부(1492000) 소관 현행 법령 + 지난 이름(연혁)
- 판례: 참조 법령(JO) 조건으로 법령마다 받아 합친다.
- 헌재결정례·법제처해석례: JO 조건을 받지 않아 법령 이름 검색으로 후보를 받고, 제목(또는 상세의
  심판대상조문·참조조문/질의요지)에 소관 법령 이름이 그대로 나오는 것만 남긴다. 상세를 본 판정은
  `lawgokr_scope_checks` 에 남겨 다음 날 다시 묻지 않는다(법령 목록이 바뀌면 다시 판정).
"""

import concurrent.futures as cf
import hashlib
import logging
import re
import threading
from datetime import datetime
from typing import Callable, Dict, Iterable, Iterator, List, Optional

import requests

from scripts.core.config import LAWGO_OC
from scripts.core.run_report import call_with_retry

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
TIMEOUT_SECONDS = 30
PAGE_SIZE = 100
MAX_WORKERS = 4

# 법령 목록은 두 부처 소관을 받지만, 판례·헌재·법제처해석례 범위는 옛 '소관부처 분류'처럼 고용노동부만 본다
# (개인정보 보호법을 넣으면 개인정보보호법위반 형사 판례가 수백 건 섞인다 — 11-15 리허설).
ORG_CODES = (("1492000", "고용노동부"), ("1790365", "개인정보보호위원회"))
SCOPE_ORG_CODES = ("1492000",)
SCOPE_CHECK_COLLECTION = "lawgokr_scope_checks"


class OpenApiError(RuntimeError):
    """law.go.kr Open API 가 결과 대신 오류·안내를 돌려줌(요약의 원인 분류가 'Open API' 로 잡는다)."""


def _get_body(url: str, params: dict, get: Callable = requests.get) -> dict:
    resp = get(url, params={**params, "OC": LAWGO_OC, "type": "JSON"}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise OpenApiError(f"law.go.kr Open API 응답이 JSON 이 아님({params.get('target')})") from exc
    body = next(iter(data.values()), None) if isinstance(data, dict) and data else None
    if not isinstance(body, dict):
        raise OpenApiError(f"law.go.kr Open API 오류 응답({params.get('target')}): {str(body)[:120]}")
    return body


def _rows(body: dict, row_keys: Iterable[str]) -> list:
    for key in row_keys:
        rows = body.get(key)
        if rows:
            return [rows] if isinstance(rows, dict) else list(rows)
    return []


def search(target: str, params: Optional[dict] = None, *, row_keys: Iterable[str] = (),
           max_pages: Optional[int] = None, get: Callable = requests.get) -> Iterator[dict]:
    """목록 검색 결과 행을 쪽마다 받아 차례로 내놓는다. 쪽마다 몇 번 더 시도하고, 끝내 못 받으면 예외를 올린다."""
    keys = tuple(row_keys) or (target, target[:1].upper() + target[1:])
    page = 1
    while True:
        query = {"target": target, "display": PAGE_SIZE, "page": page, **(params or {})}
        body = call_with_retry(lambda: _get_body(SEARCH_URL, query, get))
        rows = _rows(body, keys)
        yield from rows
        total = int(str(body.get("totalCnt") or "0").replace(",", "") or 0)
        if len(rows) < PAGE_SIZE or page * PAGE_SIZE >= total or (max_pages and page >= max_pages):
            return
        page += 1


def service(target: str, get: Callable = requests.get, **params) -> dict:
    """상세 한 건. 없거나 오류면 OpenApiError."""
    return call_with_retry(lambda: _get_body(SERVICE_URL, {"target": target, **params}, get))


# ---------------------------------------------------------------------------
# 소관 법령 기준
# ---------------------------------------------------------------------------

_laws_cache: Dict[str, Dict[str, List[str]]] = {}
_laws_lock = threading.Lock()


def jurisdiction_laws(get: Callable = requests.get) -> Dict[str, List[str]]:
    """{"current": 지금 이름, "all": 지금 이름 + 지난 이름}. 판례 JO 검색은 현행 이름, 대조는 모든 이름을 쓴다.

    한 실행에서 판례·헌재·법제처해석례가 함께 쓰므로 같은 날에는 한 번만 받는다(가짜 get 을 넘기면 매번 받는다).
    """
    if get is not requests.get:
        return _fetch_jurisdiction_laws(get)
    day = datetime.now().strftime("%Y%m%d")
    with _laws_lock:
        if day not in _laws_cache:
            _laws_cache.clear()
            _laws_cache[day] = _fetch_jurisdiction_laws(get)
        return _laws_cache[day]


def _fetch_jurisdiction_laws(get: Callable) -> Dict[str, List[str]]:
    current, history = set(), set()
    for org in SCOPE_ORG_CODES:
        current |= {r["법령명한글"] for r in search("law", {"org": org}, get=get) if r.get("법령명한글")}
        history |= {r["법령명한글"] for r in search("eflaw", {"org": org, "nw": "1"}, row_keys=("law",), get=get)
                    if r.get("법령명한글")}
    if not current:
        raise OpenApiError("law.go.kr Open API 소관 법령 목록이 비어 있음")
    return {"current": sorted(current), "all": sorted(current | history)}


def names_fingerprint(names: Iterable[str]) -> str:
    return hashlib.sha1("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:16]


class LawNameMatcher:
    """글 안에 소관 법령 이름이 그대로 나오는지. 띄어쓰기 차이(옛 법령명은 붙여 씀)는 무시한다.

    앞 글자가 한글이면 다른 법령 이름의 일부로 본다(경제사회노동위원회법 ≠ 노동위원회법). '구 ○○법'은 받는다.
    """

    def __init__(self, names: Iterable[str]):
        parts = []
        for name in sorted({n for n in names if n}, key=len, reverse=True):
            chars = [re.escape(c) for c in re.sub(r"\s+", "", name)]
            parts.append(r"\s*".join(chars))
        self._pattern = re.compile(r"(?<![가-힣])(?:" + "|".join(parts) + ")") if parts else None

    def cites(self, text: str) -> bool:
        return bool(self._pattern and text and self._pattern.search(text))


# ---------------------------------------------------------------------------
# 범위 판정 기록
# ---------------------------------------------------------------------------

class ScopeCheckStore:
    """상세를 보고 내린 범위 판정을 Mongo 에 남긴다. DB 를 못 쓰면 기록 없이 매번 판정한다."""

    def __init__(self, collection_name: str, fingerprint: str, db=None):
        self.collection_name = collection_name
        self.fingerprint = fingerprint
        self._db = db

    def _coll(self):
        if self._db is None:
            from scripts.core.database.mongo_client import get_mongo_db
            self._db = get_mongo_db()
        return self._db[SCOPE_CHECK_COLLECTION]

    def load(self, ids: Iterable[str]) -> Dict[str, bool]:
        ids = list(ids)
        try:
            found = {}
            for start in range(0, len(ids), 1000):
                for doc in self._coll().find({"collection": self.collection_name,
                                              "doc_id": {"$in": ids[start:start + 1000]},
                                              "fingerprint": self.fingerprint}):
                    found[doc["doc_id"]] = bool(doc["in_scope"])
            return found
        except Exception as exc:  # noqa: BLE001 - 기록을 못 읽어도 판정은 할 수 있다
            logger.warning("범위 판정 기록 읽기 실패(전부 다시 판정): %s", type(exc).__name__)
            return {}

    def save(self, decisions: Dict[str, bool]) -> None:
        if not decisions:
            return
        try:
            from pymongo import UpdateOne
            now = datetime.now().isoformat()
            self._coll().bulk_write([
                UpdateOne({"collection": self.collection_name, "doc_id": doc_id},
                          {"$set": {"in_scope": in_scope, "fingerprint": self.fingerprint, "checked_at": now}},
                          upsert=True)
                for doc_id, in_scope in decisions.items()], ordered=False)
        except Exception as exc:  # noqa: BLE001 - 기록 실패는 다음 날 다시 판정하면 된다
            logger.warning("범위 판정 기록 저장 실패: %s", type(exc).__name__)


def scoped_candidates(*, target: str, id_key: str, title_key: str, detail_fields: Iterable[str],
                      collection_name: str, laws: Dict[str, List[str]], store: Optional[ScopeCheckStore] = None,
                      get: Callable = requests.get, on_failure: Optional[Callable[[str, Exception], None]] = None,
                      max_workers: int = MAX_WORKERS) -> List[dict]:
    """법령 이름 검색(제목·본문)으로 후보를 모으고 소관 법령을 인용하는 것만 돌려준다(헌재결정례·법제처해석례).

    제목에 법령 이름이 있으면 바로 넣고, 없으면 상세의 detail_fields 를 본다(판정은 store 에 남김).
    """
    matcher = LawNameMatcher(laws["all"])
    candidates: Dict[str, dict] = {}

    def collect(name: str, mode: str) -> List[dict]:
        return list(search(target, {"query": name, "search": mode}, get=get))

    jobs = [(name, mode) for name in laws["current"] for mode in ("1", "2")]
    with cf.ThreadPoolExecutor(max_workers) as pool:
        futures = {pool.submit(collect, name, mode): (name, mode) for name, mode in jobs}
        for future in cf.as_completed(futures):
            name, mode = futures[future]
            try:
                for row in future.result():
                    doc_id = str(row.get(id_key) or "")
                    if doc_id:
                        candidates.setdefault(doc_id, row)
            except Exception as exc:  # noqa: BLE001 - 법령 하나 검색 실패는 기록하고 나머지를 모은다
                logger.warning("%s 검색 실패(%s, search=%s): %s", target, name, mode, exc)
                if on_failure:
                    on_failure(f"list-query:{name}", exc)

    kept = {i: r for i, r in candidates.items() if matcher.cites(str(r.get(title_key) or ""))}
    undecided = [i for i in candidates if i not in kept]
    store = store or ScopeCheckStore(collection_name, names_fingerprint(laws["all"]))
    known = store.load(undecided)
    kept.update({i: candidates[i] for i, ok in known.items() if ok})
    to_check = [i for i in undecided if i not in known]

    def judge(doc_id: str) -> Optional[bool]:
        try:
            body = service(target, get=get, ID=doc_id)
        except Exception as exc:  # noqa: BLE001 - 판정 못 한 문서는 기록하지 않고 다음 날 다시 본다
            logger.warning("%s 상세 판정 실패(%s): %s", target, doc_id, exc)
            return None
        return matcher.cites(" ".join(str(body.get(f) or "") for f in detail_fields))

    decisions: Dict[str, bool] = {}
    with cf.ThreadPoolExecutor(max_workers) as pool:
        for doc_id, verdict in zip(to_check, pool.map(judge, to_check)):
            if verdict is not None:
                decisions[doc_id] = verdict
                if verdict:
                    kept[doc_id] = candidates[doc_id]
    store.save(decisions)
    logger.info("%s 후보 %d건 → 제목 일치 %d · 기록 %d · 새 판정 %d(통과 %d) → %d건",
                target, len(candidates), len(candidates) - len(undecided), len(known), len(decisions),
                sum(decisions.values()), len(kept))
    return list(kept.values())
