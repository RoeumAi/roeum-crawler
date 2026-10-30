"""테스트용 가짜 MongoDB. 네트워크·실제 DB 없이 저장 로직의 결과 행을 확인한다."""


def _get(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _has(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _set(doc, path, value):
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _unset(doc, path):
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.get(part)
        if not isinstance(cur, dict):
            return
    cur.pop(parts[-1], None)


def _match(filter_, doc):
    for key, cond in (filter_ or {}).items():
        if key == "$or":
            if not any(_match(sub, doc) for sub in cond):
                return False
            continue
        value = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$in" and value not in arg:
                    return False
                if op == "$ne" and value == arg:
                    return False
                if op == "$exists" and _has(doc, key) != bool(arg):
                    return False
        elif value != cond:
            return False
    return True


class _Result:
    def __init__(self, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.index_calls = 0
        self.fail_update = None  # 문서를 받아 True 를 돌려주면 update_one 이 예외를 던진다
        self._next_id = 1

    def create_index(self, *args, **kwargs):
        self.index_calls += 1

    def find(self, filter_=None, projection=None):
        return [d for d in self.docs if _match(filter_, d)]

    def find_one(self, filter_=None, projection=None):
        found = self.find(filter_)
        return found[0] if found else None

    def distinct(self, field, filter_=None):
        return [_get(d, field) for d in self.find(filter_)]

    def _apply(self, doc, update, inserting):
        for key, value in update.get("$set", {}).items():
            _set(doc, key, value)
        for key in update.get("$unset", {}):
            _unset(doc, key)
        if inserting:
            for key, value in update.get("$setOnInsert", {}).items():
                _set(doc, key, value)

    def update_one(self, filter_, update, upsert=False):
        if self.fail_update is not None and self.fail_update(update, filter_):
            raise RuntimeError("write failed")
        for doc in self.docs:
            if _match(filter_, doc):
                self._apply(doc, update, False)
                return _Result(modified_count=1)
        if upsert:
            doc = {k: v for k, v in filter_.items() if not k.startswith("$") and not isinstance(v, dict)}
            self._apply(doc, update, True)
            doc["_id"] = self._next_id
            self._next_id += 1
            self.docs.append(doc)
            return _Result(upserted_id=doc["_id"])
        return _Result()

    def update_many(self, filter_, update):
        count = 0
        for doc in self.docs:
            if _match(filter_, doc):
                self._apply(doc, update, False)
                count += 1
        return _Result(modified_count=count)

    def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", self._next_id)
        self._next_id += 1
        self.docs.append(doc)
        return _Result(upserted_id=doc["_id"])


class FakeDB(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]
