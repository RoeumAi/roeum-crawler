"""
MongoDB 연결 관리 (싱글톤 패턴)

최소 구현: 연결만 관리
"""
import os
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
import logging

logger = logging.getLogger(__name__)


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
        # .env에서 설정 읽기 (없으면 기본값)
        mongo_uri = os.getenv(
            "MONGO_URI",
            "mongodb+srv://loum_chanhee:1G2BLczQ1nipQvXC@loum.veydouo.mongodb.net/?retryWrites=true&w=majority&appName=loum"
        )
        db_name = os.getenv("MONGO_DB_NAME", "original_db")
        
        try:
            MongoClientSingleton._client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
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
