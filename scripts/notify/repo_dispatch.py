#!/usr/bin/env python3
"""일일 크롤링이 끝나면 b2c 저장소에 repository_dispatch(legaldb-changed)를 보낸다.

b2c 의 워크플로가 이 신호를 받아 공개 법률 문서 페이지를 다시 만든다. run_daily.sh 가
`python -m scripts.notify.repo_dispatch` 로 부른다. 크롤링 결과와 무관한 부가 알림이라
토큰이 없거나 요청이 실패해도 예외를 던지지 않고 항상 종료 코드 0 으로 끝난다.
토큰은 환경변수 LEGALDB_DISPATCH_TOKEN(ECS 는 SSM 시크릿)으로만 받고 로그에 남기지 않는다.
"""
import json
import os
import urllib.request

TOKEN_ENV = "LEGALDB_DISPATCH_TOKEN"
DISPATCH_URL = "https://api.github.com/repos/RoeumAi/roeum-web-b2c/dispatches"
EVENT_TYPE = "legaldb-changed"


def dispatch_legaldb_changed() -> bool:
    """디스패치를 보내 성공하면 True. 토큰이 없거나 실패하면 False(예외는 밖으로 나가지 않는다)."""
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        print(f"법률 문서 페이지 갱신 요청 건너뜀: {TOKEN_ENV} 없음")
        return False
    try:
        req = urllib.request.Request(
            DISPATCH_URL,
            data=json.dumps({"event_type": EVENT_TYPE}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "roeum-crawler",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = 200 <= resp.status < 300
        print("법률 문서 페이지 갱신 요청 전송 완료" if ok else f"법률 문서 페이지 갱신 요청 실패: HTTP {resp.status}")
        return ok
    except Exception as e:  # 크롤링 결과에 영향 없음. 메시지에 토큰이 섞일 수 있어 타입만 남긴다.
        print(f"법률 문서 페이지 갱신 요청 실패: {type(e).__name__}")
        return False


def main() -> int:
    dispatch_legaldb_changed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
