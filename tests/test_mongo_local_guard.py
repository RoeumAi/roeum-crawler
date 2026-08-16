import pytest
from scripts.core.database.mongo_client import assert_local_mongo_target


def test_raises_when_local_env_targets_atlas():
    with pytest.raises(RuntimeError):
        assert_local_mongo_target("local", "mongodb+srv://u:p@loum.veydouo.mongodb.net/original_db")


def test_allows_localhost_in_local_env():
    assert_local_mongo_target("local", "mongodb://localhost:27017/original_db_local")
    assert_local_mongo_target("LOCAL", "mongodb://127.0.0.1:27017/x")


def test_noop_when_env_not_local():
    # local이 아니면 Atlas URI라도 통과(운영/Mac mini 동작 불변)
    assert_local_mongo_target("", "mongodb+srv://u:p@loum.veydouo.mongodb.net/original_db")
    assert_local_mongo_target("prod", "mongodb+srv://u:p@loum.veydouo.mongodb.net/original_db")
