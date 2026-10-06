"""기간과 SHA로 입력을 고정하고 계산 결과를 불변 JSON으로 저장/조회한다."""
import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from app.models.analysis_snapshot import AnalysisSnapshot

from app.services.metric_policy import FORMULA_VERSION, LOW_SUPPORT_THRESHOLD, DEFAULT_WINDOW_DAYS, large_change_threshold


def canonical_payload(value):
    """JSONB의 숫자 표현 정리(-0.0 → 0 등) 전후에도 같은 해시를 사용한다."""
    if isinstance(value, float) and value.is_integer(): return int(value)
    if isinstance(value, dict): return {str(k): canonical_payload(v) for k,v in value.items()}
    if isinstance(value, list): return [canonical_payload(v) for v in value]
    return value


def payload_digest(payload):
    return hashlib.sha256(json.dumps(canonical_payload(payload),sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def utc_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("분석 시각에는 시간대가 필요합니다.")
    return value.astimezone(timezone.utc)


def analysis_parameters(start=None, end=None):
    end = utc_datetime(end) if end else datetime.now(timezone.utc)
    start = utc_datetime(start) if start else end - timedelta(days=DEFAULT_WINDOW_DAYS)
    if start and start >= end:
        raise ValueError("분석 시작 시각은 종료 시각보다 이전이어야 합니다.")
    if end > datetime.now(timezone.utc) + timedelta(seconds=5):
        raise ValueError("미래 시각으로 분석할 수 없습니다.")
    return {"window_start": start.isoformat() if start else None,
            "window_end": end.isoformat(), "formula_version": FORMULA_VERSION,
            "interval": "[start,end)", "low_support_threshold": LOW_SUPPORT_THRESHOLD,
            "score_kind": "maintenance_priority", "normalization": "percentile_midrank",
            "priority_weights": {"change_freq": 1/3, "churn": 1/3, "bugfix_count": 1/3},
            "large_change_policy": "max(15,ceil(0.1*target_tree_files))",
            "large_change_size_basis": "target_tree_proxy"}


class SnapshotInput:
    """분석기의 SELECT를 기간·기본 브랜치 SHA·파일 트리에 한정한다.

    원본 테이블을 변경하거나 임시 테이블을 생성하지 않는다. 기존 SQL의 WITH 앞에
    범위 CTE를 붙이며, public.*는 CTE와 구분되는 원본이다. PostgreSQL 전용이다.
    """
    def __init__(self, db, repository_id, parameters, live_paths, reachable_shas):
        self.db = db
        self.cache = {}
        end = utc_datetime(parameters["window_end"])
        start = utc_datetime(parameters["window_start"]) if parameters.get("window_start") else end - timedelta(days=DEFAULT_WINDOW_DAYS)
        self.info = {"analysis_as_of": end, "analysis_window_start": start}
        self.params = {
            "snapshot_rid": repository_id,
            "snapshot_start": start,
            "snapshot_end": self.info["analysis_as_of"],
            "snapshot_paths": live_paths,
            "snapshot_shas": reachable_shas,
            "snapshot_large_threshold": large_change_threshold(len(live_paths)),
        }

    def query(self, model):
        # 기존 분석기의 ORM 조회는 Repository 메타데이터만 사용한다.
        return self.db.query(model)

    def execute(self, statement, params=None):
        cache_key = (str(statement), json.dumps(params or {}, sort_keys=True, default=str))
        if cache_key in self.cache:
            return FrozenRows(self.cache[cache_key])
        sql = str(statement).strip().replace("now()", "CAST(:snapshot_end AS timestamptz)")
        if not sql.upper().startswith(("SELECT", "WITH")):
            raise ValueError("스냅샷 입력은 SELECT만 허용합니다.")
        scopes = """
            commits AS (
                SELECT id,repository_id,contributor_id,sha,author_name,author_email,message,
                    commit_type,commit_type_source,commit_type_confidence,changed_file_count,
                    COALESCE(changed_file_count,(SELECT COUNT(DISTINCT f.file_id) FROM public.commit_file_changes f WHERE f.commit_id=public.commits.id)) >= :snapshot_large_threshold AS is_large_commit,
                    committed_at,true AS on_default_branch
                FROM public.commits WHERE repository_id = :snapshot_rid
                AND committed_at >= :snapshot_start AND committed_at < :snapshot_end
                AND sha = ANY(:snapshot_shas)
            ),
            commit_file_changes AS (
                SELECT f.* FROM public.commit_file_changes f JOIN commits c ON c.id=f.commit_id
            ),
            pull_requests AS (
                SELECT p.id,p.repository_id,p.pr_number,p.title,p.state,p.author_login,p.author_contributor_id,
                    p.opened_at,p.merged_at,p.closed_at,p.changed_file_count,
                    p.base_branch,p.head_branch,p.review_duration_hours,p.merged_commit_sha,
                    p.file_collection_status,p.file_collection_reason,
                    p.files_collected_count,p.files_total_count,
                    COALESCE(p.changed_file_count,(SELECT COUNT(DISTINCT f.file_id) FROM public.pull_request_files f WHERE f.pull_request_id=p.id)) >= :snapshot_large_threshold AS is_large_pr
                FROM public.pull_requests p WHERE repository_id = :snapshot_rid
                AND merged_at >= :snapshot_start AND merged_at < :snapshot_end
                AND state = 'merged'
            ),
            pull_request_files AS (
                SELECT f.* FROM public.pull_request_files f JOIN pull_requests p ON p.id=f.pull_request_id
            ),
            pr_commits AS (
                SELECT pc.* FROM public.pr_commits pc JOIN pull_requests p ON p.id=pc.pull_request_id
                JOIN commits c ON c.id=pc.commit_id
            ),
            pr_reviews AS (
                SELECT r.* FROM public.pr_reviews r JOIN pull_requests p ON p.id=r.pull_request_id
                WHERE r.submitted_at >= :snapshot_start AND r.submitted_at < :snapshot_end
            ),
            contributors AS (
                SELECT co.* FROM public.contributors co WHERE co.repository_id=:snapshot_rid
                AND (co.id IN (SELECT contributor_id FROM commits)
                     OR co.id IN (SELECT author_contributor_id FROM pull_requests)
                     OR co.id IN (SELECT reviewer_contributor_id FROM pr_reviews))
            ),
            files AS (
                SELECT f.id,f.repository_id,f.path,f.extension,f.created_at,
                       NOT (f.path = ANY(:snapshot_paths)) AS is_deleted
                FROM public.files f WHERE f.repository_id=:snapshot_rid
                AND (f.path = ANY(:snapshot_paths) OR f.id IN (SELECT file_id FROM commit_file_changes))
            ),
            releases AS (
                SELECT * FROM public.releases WHERE repository_id=:snapshot_rid
                AND published_at >= :snapshot_start AND published_at < :snapshot_end
            )
        """
        scopes = scopes.replace(' AS (', ' AS NOT MATERIALIZED (')
        for name in ['commits', 'commit_file_changes', 'files']:
            scopes = scopes.replace(f'{name} AS NOT MATERIALIZED (', f'{name} AS MATERIALIZED (')
        sql = "WITH " + scopes + (", " + sql[4:].lstrip() if sql.upper().startswith("WITH") else " " + sql)
        result = self.db.execute(text(sql), {**(params or {}), **self.params})
        # 동일 스냅샷 내 여러 지표가 공유하는 SELECT는 한 번만 실행한다.
        if hasattr(result, 'mappings'):
            rows = [dict(r) for r in result.mappings().all()]
            self.cache[cache_key] = rows
            return FrozenRows(rows)
        return result


class FrozenRows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return copy.deepcopy(self.rows)
    def __iter__(self): return iter(self.all())
    def first(self): return copy.deepcopy(self.rows[0]) if self.rows else None
    def one(self):
        if len(self.rows) != 1: raise ValueError('결과가 한 행이어야 합니다.')
        return self.first()
    def scalar(self): return next(iter(self.rows[0].values())) if self.rows else None


def data_quality(db, rid):
    counts = dict(db.execute(text("""
        SELECT COUNT(*) AS commit_count,
               COUNT(*) FILTER (WHERE contributor_id IS NULL) AS unidentified_commits,
               COUNT(*) FILTER (WHERE commit_type_confidence < 0.95) AS low_rule_confidence_commits
        FROM commits WHERE repository_id=:rid
    """), {"rid": rid}).mappings().one())
    identities = dict(db.execute(text("""
        SELECT COUNT(*) AS contributor_count,
               COUNT(*) FILTER (WHERE github_login IS NULL) AS github_account_unlinked
        FROM contributors WHERE repository_id=:rid
    """), {"rid": rid}).mappings().one())
    prs = dict(db.execute(text("""
        SELECT COUNT(*) AS merged_pr_count,
            COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM pull_request_files f WHERE f.pull_request_id=p.id)) AS prs_with_files,
            COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM pr_commits pc WHERE pc.pull_request_id=p.id AND pc.commit_id IS NOT NULL)) AS prs_with_linked_commits,
            -- 아래는 수집 담당이 기록한 '상세 수집 완전성'이다. prs_with_files(상세가
            -- 1행이라도 있는 PR 수)는 보유율일 뿐 완전성을 보장하지 않으므로 함께 쓴다.
            COUNT(*) FILTER (WHERE p.file_collection_status = 'complete') AS prs_detail_complete,
            COUNT(*) FILTER (WHERE p.file_collection_status = 'partial') AS prs_detail_partial,
            COUNT(*) FILTER (WHERE p.file_collection_status = 'failed') AS prs_detail_failed,
            COUNT(*) FILTER (WHERE p.file_collection_status IS NULL) AS prs_detail_unknown,
            COALESCE(SUM(p.files_collected_count), 0) AS pr_files_collected,
            COALESCE(SUM(p.files_total_count), 0) AS pr_files_total
        FROM pull_requests p WHERE repository_id=:rid
    """), {"rid": rid}).mappings().one())
    # 실패 사유별 PR 수 (unauthenticated / forbidden / rate_limited / page_error / api_file_limit)
    prs["pr_detail_failure_reasons"] = {
        r["file_collection_reason"]: r["cnt"]
        for r in db.execute(text("""
            SELECT file_collection_reason, COUNT(*) AS cnt
            FROM pull_requests
            WHERE repository_id=:rid AND file_collection_reason IS NOT NULL
            GROUP BY 1
        """), {"rid": rid}).mappings()
    }
    return {**counts, **identities, **prs, "low_support_threshold": LOW_SUPPORT_THRESHOLD,
            "coverage_scope": "수집된 이력 기준. GitHub 전체 이력의 수집 완전성을 보장하지 않음"}


def build_snapshot(db, repo, job, *, target_sha, live_paths, reachable_shas, parameters, persist=True):
    parameters = {**parameters, **analysis_parameters(parameters.get('window_start'), parameters['window_end'])}
    from app.api import analytics as a
    scoped = SnapshotInput(db, repo.id, parameters, live_paths, reachable_shas)
    unlimited = 2_147_483_647
    payload = {
        "summary": a.get_summary(repo.id, db=scoped),
        "timeline": a.get_timeline(repo.id, months=0, db=scoped),
        "commit-types": a.get_commit_types(repo.id, db=scoped),
        "releases": a.get_releases(repo.id, limit=unlimited, db=scoped),
        "top-contributors": a.get_top_contributors(repo.id, limit=unlimited, db=scoped),
        "risk-scores": a.get_risk_scores(repo.id, limit=unlimited, db=scoped),
        "bus-factor": a.get_bus_factor_analysis(repo.id, limit=unlimited, db=scoped),
        "coupling": a.get_coupling(repo.id, limit=unlimited, db=scoped),
        "network": a.get_network(repo.id, limit_nodes=unlimited, db=scoped),
        "network/top-pairs": a.get_network_top_pairs(repo.id, limit=unlimited, db=scoped),
        "tree": a.get_repo_tree(repo.id, db=scoped),
        "ownership": a._fetch_recent_ownership(scoped, repo.id),
        "file_index": [dict(r) for r in scoped.execute(text("SELECT id,path FROM files WHERE repository_id=:rid"), {"rid": repo.id}).mappings()],
        "contributor_index": [dict(r) for r in scoped.execute(text("SELECT id,display_name,github_login FROM contributors WHERE repository_id=:rid"), {"rid": repo.id}).mappings()],
        "weekly_activity": [dict(r) for r in scoped.execute(text("""
            SELECT contributor_id,to_char(date_trunc('week',committed_at),'YYYY-MM-DD') AS week,COUNT(*) AS count
            FROM commits WHERE repository_id=:rid AND contributor_id IS NOT NULL GROUP BY 1,2
        """), {"rid": repo.id}).mappings()],
        "data_quality": data_quality(scoped, repo.id),
    }
    payload["summary"]["repository"]["target_sha"] = target_sha
    payload["summary"]["repository"]["last_analyzed_at"] = parameters["window_end"]
    payload["ownership"] = {str(fid): a._ownership_analysis(rows) for fid, rows in payload["ownership"].items()}
    payload["data_quality"].update(target_tree_file_count=len(live_paths), collected_target_file_count=len(payload["tree"]["files"]))
    payload = canonical_payload(jsonable_encoder(payload))
    digest = payload_digest(payload)
    snapshot = AnalysisSnapshot(repository_id=repo.id, analysis_job_id=job.id if job else None,
        target_sha=target_sha, analyzed_at=datetime.now(timezone.utc),
        window_start=utc_datetime(parameters["window_start"]) if parameters["window_start"] else None,
        window_end=utc_datetime(parameters["window_end"]), formula_version=FORMULA_VERSION,
        parameters={**parameters,"hash_format":"canonical-json-numbers-v1"}, payload=payload, payload_sha256=digest)
    if persist:
        db.add(snapshot)
        db.flush()
    return snapshot


def snapshot_metadata(snapshot):
    return jsonable_encoder({"id": snapshot.id, "target_sha": snapshot.target_sha,
        "analyzed_at": snapshot.analyzed_at, "window_start": snapshot.window_start,
        "window_end": snapshot.window_end, "formula_version": snapshot.formula_version,
        "parameters": snapshot.parameters, "payload_sha256": snapshot.payload_sha256})


def load_snapshot(db, repository_id, snapshot_id=None):
    query = db.query(AnalysisSnapshot).filter(AnalysisSnapshot.repository_id == repository_id)
    if snapshot_id is not None:
        query = query.filter(AnalysisSnapshot.id == snapshot_id)
    snapshot = query.order_by(AnalysisSnapshot.id.desc()).first()
    if not snapshot:
        raise HTTPException(409 if snapshot_id is None else 404,
                            "저장된 분석 결과가 없습니다. 분석 또는 기존 데이터 스냅샷 생성을 실행해 주세요.")
    if snapshot.parameters.get('hash_format') != 'canonical-json-numbers-v1':
        raise HTTPException(409,"이 결과의 저장 형식이 갱신됐습니다. 최신 분석 결과를 다시 열어 주세요.")
    if payload_digest(snapshot.payload) != snapshot.payload_sha256:
        raise HTTPException(409,"저장된 결과의 무결성 검증에 실패했습니다.")
    return snapshot


def bounded(query, key, default, lo=0, hi=10000, integer=True):
    try:
        value = int(query.get(key, default)) if integer else float(query.get(key, default))
    except (TypeError, ValueError):
        raise HTTPException(422, f"유효하지 않은 {key}")
    import math
    if not math.isfinite(value) or not lo <= value <= hi:
        raise HTTPException(422, f"{key} 범위: {lo}~{hi}")
    return value


FILE_METRIC_FIELDS = ("maintenance_priority", "risk_score", "priority_components", "unidentified_share",
                      "window_start", "window_end", "evidence_status", "change_freq", "churn", "bugfix_count",
                      "current_risk", "structural_risk")


def file_metric_fields(payload, file_id):
    """파일 상세에 싣는 점수 구성과 근거 상태.

    risk-scores는 관측 구간에 변경이 있는 파일을 전부 담는다(HTTP 응답만 limit으로 자른다).
    거기 없는 파일은 트리(현재 기준 SHA의 파일 전체)에 기록된 상태를 쓴다. 둘 다 없으면 상태를
    만들어 내지 않는다 — 호출부(화면)가 '상위 N개에 없다'는 이유로 상태를 추정하지 않게 하려는 것이다."""
    scored = next((f for f in payload.get("risk-scores", {}).get("files", []) if f["file_id"] == file_id), None)
    if scored is not None:
        return {k: scored[k] for k in FILE_METRIC_FIELDS if k in scored}
    in_tree = next((f for f in payload.get("tree", {}).get("files", []) if f["file_id"] == file_id), None)
    if in_tree is None:
        return {}
    return {k: in_tree[k] for k in ("maintenance_priority", "risk_score", "evidence_status") if k in in_tree}


def serve_snapshot(snapshot, route, query):
    """저장된 값만 선택/필터링한다. 지표 재계산이나 원본 DB 조회 없음."""
    p = snapshot.payload
    limit = bounded(query, "limit", 50)
    if route == "snapshot":
        result = {"snapshot": snapshot_metadata(snapshot)}
    elif route in {"summary", "commit-types", "tree"}:
        result = copy.deepcopy(p[route])
    elif route == "timeline":
        from dateutil.relativedelta import relativedelta
        months = bounded(query, "months", 36, lo=-1, hi=2400)
        start = utc_datetime(snapshot.window_end) - relativedelta(months=months) if months > 0 else None
        indices = [i for i, month in enumerate(p[route]["months"]) if start is None or month >= start.strftime("%Y-%m")]
        result = {key: [values[i] for i in indices] for key, values in p[route].items()}
    elif route in {"releases", "top-contributors", "risk-scores"}:
        key = {"releases": "releases", "top-contributors": "contributors", "risk-scores": "files"}[route]
        defaults = {"releases": 5, "top-contributors": 5, "risk-scores": 50}
        result = copy.deepcopy(p[route])
        result[key] = result[key][:bounded(query, "limit", defaults[route])]
    elif route == "bus-factor":
        result = copy.deepcopy(p[route])
        for key in ["top_owners", "risky_modules", "files"]:
            result[key] = result[key][:limit]
    elif route == "coupling":
        minimum = bounded(query, "min_score", 0, hi=1, integer=False)
        sort_by = query.get("sort_by", "coupling")
        if sort_by not in {"coupling", "idf"}:
            raise HTTPException(422,"sort_by는 coupling 또는 idf입니다.")
        pairs = [x for x in p[route]["pairs"] if x["coupling_score"] >= minimum]
        key = "adjusted_score" if sort_by == "idf" else "coupling_score"
        pairs = sorted(pairs,key=lambda x:(-x[key],-x['co_change_count'],x['file_a_id'],x['file_b_id']))
        result = {"meta": {**p[route]["meta"],"sort_by":sort_by}, "pairs": pairs[:bounded(query,"limit",100)]}
    elif route == "network":
        count = bounded(query, "limit_nodes", 50, lo=1, hi=1000)
        minimum = bounded(query, "min_edge_weight", 0, integer=False)
        nodes = sorted(p[route]["nodes"], key=lambda n: (-n["commit_count"], n["id"]))[:count]
        ids = {n["id"] for n in nodes}
        result = {"nodes": nodes, "metrics": p[route]["metrics"], "edges": [e for e in p[route]["edges"] if e["source"] in ids and e["target"] in ids and e["edge_weight"] >= minimum]}
    elif route == "network/top-pairs":
        return p[route][:bounded(query, "limit", 5)]
    elif route == "contributor-heatmap":
        weeks = bounded(query,"weeks",16,lo=1,hi=104)
        end = utc_datetime(snapshot.window_end)
        monday = end.date() - timedelta(days=end.weekday())
        week_names = [(monday - timedelta(weeks=i)).isoformat() for i in reversed(range(weeks))]
        contributors = p["top-contributors"]["contributors"][:bounded(query,"limit",8,lo=1,hi=100)]
        activity = {(r["contributor_id"],r["week"]):r["count"] for r in p["weekly_activity"]}
        result = {"weeks": week_names, "contributors": [{"id":c["id"],"name":c["github_login"] or c["display_name"] or "익명", "counts":[activity.get((c["id"],w),0) for w in week_names]} for c in contributors]}
    else:
        parts = route.split("/")
        file_routes = (len(parts)==3 and parts[0]=="files" and parts[2]=="detail") or (len(parts)==2 and parts[0]=="coupling")
        if file_routes:
            fid = int(parts[1])
            file = next((f for f in p["file_index"] if f["id"]==fid),None)
            if not file:
                raise HTTPException(404,"파일이 해당 스냅샷에 없습니다.")
            from app.api.analytics import _coupled_files_for, COUPLED_SORT_KEYS
            sort_by = query.get("sort_by")
            if sort_by is not None and sort_by not in COUPLED_SORT_KEYS:
                raise HTTPException(422,"sort_by는 coupling 또는 idf입니다.")
            ownership = p["ownership"].get(str(fid), {"entropy":None,"bus_factor":None,"shares":[]})
            # 정렬은 전체 후보에 대해 먼저 하고 그 뒤에 limit으로 자른다
            coupled = _coupled_files_for({"pairs":p["coupling"]["pairs"]},fid,sort_by)
            result = {"file":file,"co_change_unit":p["coupling"]["meta"]["co_change_unit"],
                      "sort_by":sort_by,"coupled_total":len(coupled),"coupled_files":coupled[:bounded(query,"limit",20)],
                      "entropy":round(ownership["entropy"],4) if ownership["entropy"] is not None else None,"bus_factor":ownership["bus_factor"],
                      "is_bus_factor_risk":ownership["bus_factor"]==1,
                      "editors":[{**s,"share":round(s["share"],4)} for s in ownership["shares"]]}
            result.update({k:ownership.get(k) for k in ("entropy_normalized","top_owner_share","contributor_count","observation_count","low_support")})
            result["bf80"] = ownership["bus_factor"]
            result.update(file_metric_fields(p,fid))
        elif len(parts)==3 and parts[0]=="contributors" and parts[2] in {"owned-files","collaborators"}:
            cid = int(parts[1])
            contributor = next((c for c in p["contributor_index"] if c["id"]==cid),None)
            if not contributor:
                raise HTTPException(404,"기여자가 해당 스냅샷에 없습니다.")
            result = {"contributor":{"id":cid,"name":contributor["github_login"] or contributor["display_name"] or "익명"}}
            if parts[2]=="owned-files":
                owned = []
                for f in p["tree"]["files"]:
                    oa = p["ownership"].get(str(f["file_id"]), {"bus_factor":None,"shares":[]})
                    mine = next((s for s in oa["shares"] if s["contributor_id"]==cid),None)
                    if mine:
                        owned.append({"file_id":f["file_id"],"path":f["path"],"share":round(mine["share"],4),"bus_factor":oa["bus_factor"],"is_bus_factor_risk":oa["bus_factor"]==1})
                owned.sort(key=lambda f:(-f["share"],f["file_id"]))
                result.update(owned_file_count=len(owned),files=owned[:bounded(query,"limit",20)])
            else:
                pairs = [x for x in p["network/top-pairs"] if cid in {x["contributor_a"]["id"],x["contributor_b"]["id"]}]
                result["collaborators"] = [{"contributor":x["contributor_b"] if x["contributor_a"]["id"]==cid else x["contributor_a"], "edge_weight":x["edge_weight"],"co_edit_raw":x["co_edit_raw"],
                    "review_directed_out":x["review_directed_a_to_b"] if x["contributor_a"]["id"]==cid else x["review_directed_b_to_a"],
                    "review_directed_in":x["review_directed_b_to_a"] if x["contributor_a"]["id"]==cid else x["review_directed_a_to_b"]} for x in pairs][:bounded(query,"limit",20)]
        else:
            raise HTTPException(404,"분석 결과 경로를 찾을 수 없습니다.")
    result = copy.deepcopy(result)
    result["_snapshot"] = snapshot_metadata(snapshot)
    result["data_quality"] = p["data_quality"]
    return result
