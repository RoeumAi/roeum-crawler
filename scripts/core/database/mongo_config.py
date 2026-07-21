"""MongoDB configuration without credential-bearing defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping


def resolve_mongo_uri(environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    uri = str(env.get("MONGODB_URI") or env.get("MONGO_URI") or "").strip()
    if not uri:
        raise RuntimeError("MongoDB URI is required: set MONGODB_URI or MONGO_URI")
    return uri
