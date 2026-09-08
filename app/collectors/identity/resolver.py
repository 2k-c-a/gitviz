"""Contributor Identity Resolution (업무지시서 3-6, MVP 1~2단계).

1단계: GitHub noreply 이메일 패턴에서 username 추출 → 자동 머지 (resolution_rule='noreply_pattern')
2단계: 일반 이메일을 GitHub Search API로 조회 → 일치 계정 발견 시 자동 머지 (resolution_rule='github_api')

머지 규칙:
- 같은 github_login으로 귀결되는 contributor들을 canonical(최소 id) 하나로 통합
- 머지되는 쪽의 email/name은 contributor_aliases에 보존
- commits / pull_requests / pr_reviews 의 contributor FK도 함께 이전
"""

import re
import time
from typing import Optional, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

# {id}+{username}@users.noreply.github.com (신형) / {username}@users.noreply.github.com (구형)
NOREPLY_RE = re.compile(
    r"^(?:\d+\+)?([a-z\d](?:[a-z\d-]*[a-z\d])?)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)

ALIAS_UPSERT = text("""
    INSERT INTO contributor_aliases (contributor_id, email, name, source, resolution_rule)
    VALUES (:cid, :email, :name, :source, :rule)
    ON CONFLICT (contributor_id, COALESCE(email, ''), COALESCE(name, '')) DO NOTHING
""")


def extract_noreply_login(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    m = NOREPLY_RE.match(email.strip().lower())
    return m.group(1).lower() if m else None


def _add_alias(db: Session, contributor_id: int, email, name, source: str, rule: str):
    db.execute(ALIAS_UPSERT, {
        "cid": contributor_id, "email": email, "name": name,
        "source": source, "rule": rule,
    })


def _merge_contributor(db: Session, canonical_id: int, dup_id: int, rule: str):
    """dup을 canonical로 통합: alias 보존 + FK 이전 + dup 삭제"""
    dup = db.execute(text(
        "SELECT primary_email, display_name FROM contributors WHERE id = :d"
    ), {"d": dup_id}).fetchone()
    if dup is None:
        return

    _add_alias(db, canonical_id, dup[0], dup[1], "git", rule)

    # dup이 이미 갖고 있던 alias도 canonical로 이전
    db.execute(text("""
        INSERT INTO contributor_aliases (contributor_id, email, name, source, resolution_rule)
        SELECT :c, email, name, source, resolution_rule
        FROM contributor_aliases WHERE contributor_id = :d
        ON CONFLICT (contributor_id, COALESCE(email, ''), COALESCE(name, '')) DO NOTHING
    """), {"c": canonical_id, "d": dup_id})
    db.execute(text("DELETE FROM contributor_aliases WHERE contributor_id = :d"), {"d": dup_id})

    db.execute(text("UPDATE commits SET contributor_id = :c WHERE contributor_id = :d"),
               {"c": canonical_id, "d": dup_id})
    db.execute(text("UPDATE pull_requests SET author_contributor_id = :c WHERE author_contributor_id = :d"),
               {"c": canonical_id, "d": dup_id})
    db.execute(text("UPDATE pr_reviews SET reviewer_contributor_id = :c WHERE reviewer_contributor_id = :d"),
               {"c": canonical_id, "d": dup_id})
    db.execute(text("DELETE FROM contributors WHERE id = :d"), {"d": dup_id})


def link_commits_by_email(db: Session, repository_id: int) -> int:
    """commits.contributor_id 기본 연결 (author_email = primary_email)"""
    result = db.execute(text("""
        UPDATE commits SET contributor_id = c.id
        FROM contributors c
        WHERE commits.repository_id = :rid
          AND c.repository_id = :rid
          AND commits.contributor_id IS NULL
          AND lower(commits.author_email) = c.primary_email
    """), {"rid": repository_id})
    db.commit()
    return result.rowcount


def link_commits_by_alias(db: Session, repository_id: int) -> int:
    """머지 후: alias 이메일로 남은 커밋 연결"""
    result = db.execute(text("""
        UPDATE commits SET contributor_id = a.contributor_id
        FROM contributor_aliases a
        JOIN contributors c ON c.id = a.contributor_id
        WHERE commits.repository_id = :rid
          AND c.repository_id = :rid
          AND commits.contributor_id IS NULL
          AND a.email IS NOT NULL
          AND lower(commits.author_email) = lower(a.email)
    """), {"rid": repository_id})
    db.commit()
    return result.rowcount


def link_pr_reviews_by_login(db: Session, repository_id: int) -> int:
    result = db.execute(text("""
        UPDATE pr_reviews SET reviewer_contributor_id = c.id
        FROM pull_requests pr, contributors c
        WHERE pr.id = pr_reviews.pull_request_id
          AND pr.repository_id = :rid
          AND c.repository_id = :rid
          AND pr_reviews.reviewer_contributor_id IS NULL
          AND c.github_login IS NOT NULL
          AND lower(pr_reviews.reviewer_login) = lower(c.github_login)
    """), {"rid": repository_id})
    db.commit()
    return result.rowcount


def link_pr_authors_by_login(db: Session, repository_id: int) -> int:
    result = db.execute(text("""
        UPDATE pull_requests SET author_contributor_id = c.id
        FROM contributors c
        WHERE pull_requests.repository_id = :rid
          AND c.repository_id = :rid
          AND pull_requests.author_contributor_id IS NULL
          AND pull_requests.author_login IS NOT NULL
          AND c.github_login IS NOT NULL
          AND lower(pull_requests.author_login) = lower(c.github_login)
    """), {"rid": repository_id})
    db.commit()
    return result.rowcount


def stage1_noreply(db: Session, repository_id: int) -> Dict[str, int]:
    """noreply 패턴 자동 매핑 + 동일 login 머지"""
    rows = db.execute(text("""
        SELECT id, primary_email, display_name, github_login
        FROM contributors WHERE repository_id = :rid ORDER BY id
    """), {"rid": repository_id}).fetchall()

    groups: Dict[str, dict] = {}  # login -> {"canonical": id|None, "members": [row]}
    for r in rows:
        if r[3]:
            g = groups.setdefault(r[3].lower(), {"canonical": None, "members": []})
            if g["canonical"] is None:
                g["canonical"] = r[0]
    for r in rows:
        login = extract_noreply_login(r[1])
        if not login:
            continue
        groups.setdefault(login, {"canonical": None, "members": []})["members"].append(r)

    resolved = merged = 0
    for login, g in groups.items():
        members = g["members"]
        if not members:
            continue
        canonical_id = g["canonical"] if g["canonical"] is not None else members[0][0]

        db.execute(text("""
            UPDATE contributors SET github_login = :login, resolution_status = 'auto'
            WHERE id = :cid
        """), {"login": login, "cid": canonical_id})
        resolved += 1

        for m in members:
            _add_alias(db, canonical_id, m[1], m[2], "git", "noreply_pattern")
            if m[0] != canonical_id:
                _merge_contributor(db, canonical_id, m[0], "noreply_pattern")
                merged += 1

    db.commit()
    return {"resolved": resolved, "merged": merged}


def stage2_email_search(db: Session, repository_id: int, client,
                        limit: Optional[int] = None,
                        sleep_seconds: float = 2.2,
                        retry_cooldown_days: int = 30) -> Dict[str, int]:
    """GitHub Search API로 일반 이메일 → 계정 매핑.

    Search API는 인증 시 30회/분 제한이라 호출 사이에 proactive sleep을 둔다
    (한도 초과 시에도 client.get이 헤더 기반으로 자동 대기).
    커밋 수가 많은 기여자부터 처리해 제한된 호출 수로 커버리지를 최대화한다.

    검색에 실패해도(계정 못 찾음) resolution_status는 계속 'unresolved'로 남는다 —
    "시도했지만 못 찾음"과 "아직 시도 안 함"을 구분하려고 identity_search_attempted_at에
    시도 시각을 남기고, retry_cooldown_days 안에 이미 시도한 사람은 건너뛴다. 이게 없으면
    재분석할 때마다(신규 커밋이 몇 개뿐이어도) 커밋 수 상위 `limit`명이 매번 똑같이 뽑혀서
    지난번에 이미 실패했던 검색을 그대로 반복하며 몇 분씩 낭비하게 된다.
    """
    rows = db.execute(text("""
        SELECT c.id, c.primary_email, c.display_name, COUNT(cm.id) AS commit_count
        FROM contributors c
        LEFT JOIN commits cm ON cm.contributor_id = c.id
        WHERE c.repository_id = :rid
          AND c.resolution_status = 'unresolved'
          AND c.primary_email IS NOT NULL AND c.primary_email <> ''
          AND c.primary_email NOT LIKE '%users.noreply.github.com'
          AND (
              c.identity_search_attempted_at IS NULL
              OR c.identity_search_attempted_at < NOW() - (:cooldown || ' days')::interval
          )
        GROUP BY c.id, c.primary_email, c.display_name, c.identity_search_attempted_at
        ORDER BY commit_count DESC, c.id
    """), {"rid": repository_id, "cooldown": retry_cooldown_days}).fetchall()

    if limit is not None:
        rows = rows[:limit]

    searched = resolved = merged = 0
    for cid, email, name, _count in rows:
        searched += 1
        try:
            resp = client.get(f"{client.BASE_URL}/search/users",
                              params={"q": f"{email} in:email", "per_page": 2})
            data = resp.json()
        except Exception as e:
            print(f"  search error for {email}: {e}")
            time.sleep(sleep_seconds)
            continue

        db.execute(text(
            "UPDATE contributors SET identity_search_attempted_at = NOW() WHERE id = :cid"
        ), {"cid": cid})

        if data.get("total_count") == 1 and data.get("items"):
            login = data["items"][0]["login"].lower()

            existing = db.execute(text("""
                SELECT id FROM contributors
                WHERE repository_id = :rid AND lower(github_login) = :login AND id <> :cid
            """), {"rid": repository_id, "login": login, "cid": cid}).fetchone()

            if existing:
                _add_alias(db, existing[0], email, name, "github", "github_api")
                _merge_contributor(db, existing[0], cid, "github_api")
                merged += 1
            else:
                db.execute(text("""
                    UPDATE contributors SET github_login = :login, resolution_status = 'auto'
                    WHERE id = :cid
                """), {"login": login, "cid": cid})
                _add_alias(db, cid, email, name, "github", "github_api")
            resolved += 1

        db.commit()

        if searched % 50 == 0:
            print(f"  stage2 progress: {searched}/{len(rows)} (resolved={resolved})")
        time.sleep(sleep_seconds)

    db.commit()
    return {"searched": searched, "resolved": resolved, "merged": merged}


def _norm_token(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _name_login_similar(display_name: Optional[str], email: Optional[str], login: str) -> bool:
    """단일 PR 근거일 때 오매칭 방지용 보조 검증: 이름/이메일 로컬파트와 login 유사성"""
    nl = _norm_token(login)
    if not nl:
        return False
    nn = _norm_token(display_name)
    le = _norm_token((email or "").split("@")[0])
    if nn and len(nn) >= 3 and (nl in nn or nn in nl):
        return True
    if le and len(le) >= 4 and (nl == le or nl in le or le in nl):
        return True
    return False


def stage_pr_authorship(db: Session, repository_id: int,
                        min_dominance: float = 0.8) -> Dict[str, int]:
    """PR 작성자 login ↔ 커밋 author_email 매핑 (API 호출 없음).

    근거: 어떤 이메일의 커밋이 항상 특정 login의 PR에만 등장하면 동일인 가능성이 높다.
    오매칭 방지:
    - PR 2건 이상 + 점유율 min_dominance 이상이면 채택
    - PR 1건뿐이면 점유율 100% + 이름/이메일-login 유사성까지 요구
      (메인테이너가 타인 커밋을 자기 PR로 올린 경우를 걸러냄)
    """
    pairs = db.execute(text("""
        SELECT lower(pr.author_login) AS login,
               lower(c.author_email)  AS email,
               COUNT(DISTINCT pr.id)  AS pr_count
        FROM pull_requests pr
        JOIN pr_commits pc ON pc.pull_request_id = pr.id
        JOIN commits c     ON c.id = pc.commit_id
        WHERE pr.repository_id = :rid
          AND pr.author_login IS NOT NULL
          AND c.author_email IS NOT NULL AND c.author_email <> ''
        GROUP BY 1, 2
    """), {"rid": repository_id}).fetchall()

    by_email: Dict[str, list] = {}
    for login, email, pr_count in pairs:
        by_email.setdefault(email, []).append((login, pr_count))

    unresolved = {
        r[1]: (r[0], r[2])  # email -> (id, display_name)
        for r in db.execute(text("""
            SELECT id, primary_email, display_name FROM contributors
            WHERE repository_id = :rid AND resolution_status = 'unresolved'
              AND primary_email IS NOT NULL AND primary_email <> ''
        """), {"rid": repository_id}).fetchall()
    }

    resolved = merged = 0
    for email, candidates in by_email.items():
        if email not in unresolved:
            continue
        total = sum(cnt for _, cnt in candidates)
        login, cnt = max(candidates, key=lambda x: x[1])
        dominance = cnt / total if total else 0.0
        cid, display_name = unresolved[email]

        accept = (cnt >= 2 and dominance >= min_dominance) or (
            cnt == 1 and dominance == 1.0
            and _name_login_similar(display_name, email, login)
        )
        if not accept:
            continue

        existing = db.execute(text("""
            SELECT id FROM contributors
            WHERE repository_id = :rid AND lower(github_login) = :login AND id <> :cid
            ORDER BY id LIMIT 1
        """), {"rid": repository_id, "login": login, "cid": cid}).fetchone()

        if existing:
            _add_alias(db, existing[0], email, display_name, "git", "pr_authorship")
            _merge_contributor(db, existing[0], cid, "pr_authorship")
            merged += 1
        else:
            db.execute(text("""
                UPDATE contributors SET github_login = :login, resolution_status = 'auto'
                WHERE id = :cid
            """), {"login": login, "cid": cid})
            _add_alias(db, cid, email, display_name, "git", "pr_authorship")
        resolved += 1

    db.commit()
    return {"resolved": resolved, "merged": merged}


def resolution_stats(db: Session, repository_id: int) -> Dict[str, float]:
    total, auto = db.execute(text("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE resolution_status = 'auto')
        FROM contributors WHERE repository_id = :rid
    """), {"rid": repository_id}).fetchone()
    unresolved = total - auto
    return {
        "total": total,
        "auto": auto,
        "unresolved": unresolved,
        "unresolved_ratio": round(unresolved / total, 4) if total else 0.0,
    }


def run_identity_resolution(db: Session, repository_id: int, client=None,
                            stage2_limit: Optional[int] = 0) -> Dict:
    """전체 파이프라인. stage2_limit=0이면 2단계 생략, None이면 무제한."""
    out = {}
    out["commits_linked"] = link_commits_by_email(db, repository_id)
    out["stage1"] = stage1_noreply(db, repository_id)
    out["pr_authorship"] = stage_pr_authorship(db, repository_id)
    if client is not None and stage2_limit != 0:
        out["stage2"] = stage2_email_search(db, repository_id, client, limit=stage2_limit)
    out["commits_linked_by_alias"] = link_commits_by_alias(db, repository_id)
    out["pr_reviews_linked"] = link_pr_reviews_by_login(db, repository_id)
    out["pr_authors_linked"] = link_pr_authors_by_login(db, repository_id)
    out["stats"] = resolution_stats(db, repository_id)
    return out
