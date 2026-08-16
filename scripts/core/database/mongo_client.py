"""
MongoDB 연결 관리 (싱글톤 패턴)

최소 구현: 연결만 관리
"""
import os
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)


def resolve_mongo_uri() -> str:
    """MongoDB 연결 문자열을 환경변수에서 읽는다.

    MONGODB_URI(우선) 또는 MONGO_URI 에서 읽으며, 하드코딩된 기본값은 두지 않는다.
    둘 다 없으면 운영 자격증명으로 조용히 붙는 대신 명확히 실패한다.
    """
    mongo_uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not mongo_uri:
        raise RuntimeError(
            "MONGODB_URI(또는 MONGO_URI) 환경변수가 설정되지 않았습니다. "
            "MongoDB 연결 문자열을 .env 또는 환경변수로 제공하세요."
        )
    return mongo_uri


def assert_local_mongo_target(crawler_env: str, mongo_uri: str) -> None:
    """CRAWLER_ENV=local 이면 MongoDB 대상이 로컬인지 강제한다.

    운영 MongoDB Atlas 오염을 막기 위한 가드. local 이 아니면 아무 것도 하지 않아
    운영/Mac mini 동작은 그대로 유지된다.
    """
    if (crawler_env or "").lower() != "local":
        return
    if "localhost" not in mongo_uri and "127.0.0.1" not in mongo_uri:
        raise RuntimeError(
            "CRAWLER_ENV=local 인데 MONGODB_URI가 로컬(localhost/127.0.0.1)을 "
            "가리키지 않습니다. 운영 Atlas 오염 방지를 위해 중단합니다."
        )


class MongoClientSingleton:
    """MongoDB 연결을 관리하는 싱글톤"""
    
    _instance = None
    _client = None
    _db = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._connect()
            self._initialized = True
    
    def _connect(self):
        """MongoDB에 연결"""
        # .env를 환경변수로 로드 (crawl.py 등 __main__ 밖 실행 경로도 커버)
        load_dotenv()
        # 연결 문자열은 환경변수에서만 읽는다 (하드코딩 기본값 없음)
        mongo_uri = resolve_mongo_uri()
        assert_local_mongo_target(os.getenv("CRAWLER_ENV", ""), mongo_uri)
        db_name = os.getenv("MONGO_DB_NAME", "original_db")
        
        try:
            MongoClientSingleton._client = MongoClient(
                mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "60000")),
            )
            # 연결 테스트
            MongoClientSingleton._client.admin.command('ping')
            MongoClientSingleton._db = MongoClientSingleton._client[db_name]
            logger.info(f"✅ MongoDB 연결 성공: {db_name}")
        except ServerSelectionTimeoutError as e:
            logger.error(f"❌ MongoDB 연결 실패: {e}")
            logger.error("   설정 확인: MONGO_URI 환경변수")
            raise
        except Exception as e:
            logger.error(f"❌ MongoDB 오류: {e}")
            raise
    
    @classmethod
    def get_db(cls):
        """DB 인스턴스 반환 (자동 연결)"""
        if cls._db is None:
            instance = cls()
        return cls._db
    
    @classmethod
    def get_client(cls):
        """클라이언트 반환"""
        if cls._client is None:
            instance = cls()
        return cls._client
    
    @classmethod
    def close(cls):
        """연결 종료"""
        if cls._client:
            cls._client.close()
            cls._instance = None
            cls._client = None
            cls._db = None
            logger.info("MongoDB 연결 종료")


# 편의 함수
def get_mongo_db():
    """간단히 DB 인스턴스 얻기"""
    return MongoClientSingleton.get_db()


# 테스트용
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    
    # .env 파일 로드
    load_dotenv()
    
    try:
        db = get_mongo_db()
        print(f"✅ 연결 성공! 데이터베이스: {db.name}")
        
        # 간단한 테스트
        result = db['test_collection'].insert_one({"test": "hello"})
        print(f"✅ 삽입 테스트 성공: {result.inserted_id}")
        
        # 삽입된 데이터 확인
        doc = db['test_collection'].find_one({"_id": result.inserted_id})
        print(f"✅ 조회 테스트 성공: {doc}")
        
        # 정리
        db['test_collection'].delete_many({})
        
    except Exception as e:
        print(f"❌ 오류: {e}")
        sys.exit(1)
