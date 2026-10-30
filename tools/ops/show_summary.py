"""GitViz 데이터 현황 한눈에 보기 (읽기 전용).

사용법: python tools/ops/show_summary.py [repository_id=1]
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from app.core.database import SessionLocal

RID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
db = SessionLocal()


def scalar(q):
    return db.execute(text(q), {"r": RID}).scalar()


print("=" * 55)
print(" GitViz 데이터 현황")
print("=" * 55)

print("\n[ 저장소 ]")
for row in db.execute(text("SELECT id, url, name FROM repositories ORDER BY id")).fetchall():
    print(f"  id={row[0]}  {row[2]}  ({row[1]})")

print(f"\n[ repository_id={RID} 적재량 ]")
print(f"  커밋(commits)            : {scalar('SELECT COUNT(*) FROM commits WHERE repository_id=:r'):>8,}")
print(f"  파일(files)              : {scalar('SELECT COUNT(*) FROM files WHERE repository_id=:r'):>8,}")
print(f"  기여자(contributors)     : {scalar('SELECT COUNT(*) FROM contributors WHERE repository_id=:r'):>8,}")
print(f"  PR(pull_requests)        : {scalar('SELECT COUNT(*) FROM pull_requests WHERE repository_id=:r'):>8,}")
print(f"  릴리스(releases)         : {scalar('SELECT COUNT(*) FROM releases WHERE repository_id=:r'):>8,}")
print(f"  파일변경(commit_file_changes): {scalar('SELECT COUNT(*) FROM commit_file_changes cfc JOIN commits c ON c.id=cfc.commit_id WHERE c.repository_id=:r'):>8,}")

print(f"\n[ 기여자 신원 정리 ]")
total = scalar("SELECT COUNT(*) FROM contributors WHERE repository_id=:r")
auto = scalar("SELECT COUNT(*) FROM contributors WHERE repository_id=:r AND resolution_status='auto'")
print(f"  resolve됨(auto)          : {auto}/{total} ({auto/total*100:.1f}%)")
cw_t = scalar("SELECT COUNT(*) FROM commits WHERE repository_id=:r")
cw_a = scalar("SELECT COUNT(*) FROM commits cm JOIN contributors c ON c.id=cm.contributor_id WHERE cm.repository_id=:r AND c.resolution_status='auto'")
print(f"  커밋 가중 커버리지        : {cw_a}/{cw_t} ({cw_a/cw_t*100:.1f}%)")
print(f"  별칭(aliases) by rule    :")
for row in db.execute(text("""
    SELECT a.resolution_rule, COUNT(*) FROM contributor_aliases a
    JOIN contributors c ON c.id=a.contributor_id
    WHERE c.repository_id=:r GROUP BY a.resolution_rule ORDER BY COUNT(*) DESC
"""), {"r": RID}).fetchall():
    print(f"      {row[0] or '(none)':22s}: {row[1]}")

print(f"\n[ 커밋 분류(commit_type) ]")
for row in db.execute(text("""
    SELECT commit_type, COUNT(*) FROM commits WHERE repository_id=:r
    GROUP BY commit_type ORDER BY COUNT(*) DESC
"""), {"r": RID}).fetchall():
    print(f"  {row[0] or '(null)':10s}: {row[1]:>7,}")
large = scalar("SELECT COUNT(*) FROM commits WHERE repository_id=:r AND is_large_commit")
print(f"  대형 커밋(is_large)       : {large:,} ({large/cw_t*100:.1f}%)")

print(f"\n[ 최근 분석 작업(analysis_jobs) ]")
for row in db.execute(text("""
    SELECT id, status, started_at, completed_at FROM analysis_jobs
    WHERE repository_id=:r ORDER BY id DESC LIMIT 3
"""), {"r": RID}).fetchall():
    print(f"  job {row[0]}: {row[1]:10s} started={row[2]} completed={row[3]}")

print("\n" + "=" * 55)
db.close()
