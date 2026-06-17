"""DB / Redis / GitHub 토큰 연결 점검 (값 노출 없음)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# --- env 키 존재 여부 ---
for k in ("DATABASE_URL", "REDIS_URL", "GITHUB_TOKEN"):
    print(f"{k:14s}: {'설정됨' if os.getenv(k) else '❌ 없음'}")
print()

# --- DB ---
try:
    from sqlalchemy import text
    from app.core.database import SessionLocal
    db = SessionLocal()
    n = db.execute(text("SELECT COUNT(*) FROM repositories")).scalar()
    db.close()
    print(f"DB 연결      : ✅ OK (repositories {n}건)")
except Exception as e:
    print(f"DB 연결      : ❌ 실패 - {str(e).splitlines()[0]}")

# --- Redis ---
try:
    import redis
    r = redis.from_url(os.getenv("REDIS_URL"))
    print(f"Redis 연결   : {'✅ OK' if r.ping() else '❌ PING 실패'}")
except Exception as e:
    print(f"Redis 연결   : ❌ 실패 - {str(e).splitlines()[0]}")

# --- GitHub 토큰 ---
try:
    from app.collectors.github.github_client import GitHubClient
    c = GitHubClient()
    resp = c.get(f"{c.BASE_URL}/rate_limit")
    data = resp.json()
    core = data["resources"]["core"]
    login = "?"
    try:
        login = c.get(f"{c.BASE_URL}/user").json().get("login", "?")
    except Exception:
        login = "(user 조회 불가 - 토큰 권한 확인)"
    print(f"GitHub 토큰  : ✅ OK (계정={login}, API 잔여 {core['remaining']}/{core['limit']})")
except Exception as e:
    print(f"GitHub 토큰  : ❌ 실패 - {str(e).splitlines()[0]}")
