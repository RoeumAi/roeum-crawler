import pytest

from scripts.core.database.mongo_client import resolve_mongo_uri


def test_raises_when_no_uri_env(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    with pytest.raises(RuntimeError):
        resolve_mongo_uri()


def test_returns_mongodb_uri(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/db")
    monkeypatch.delenv("MONGO_URI", raising=False)
    assert resolve_mongo_uri() == "mongodb://localhost:27017/db"


def test_falls_back_to_mongo_uri(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017/other")
    assert resolve_mongo_uri() == "mongodb://localhost:27017/other"


def test_mongodb_uri_takes_precedence(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://primary/db")
    monkeypatch.setenv("MONGO_URI", "mongodb://secondary/db")
    assert resolve_mongo_uri() == "mongodb://primary/db"
