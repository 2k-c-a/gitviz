"""Collaboration Network view built from a stored snapshot payload (pure functions, no DB).

The snapshot keeps the original calculation untouched:
- ``network.edges`` are directional (A->B and B->A) with
  ``edge_weight = 0.6 * (co_edit_raw / 2) + 0.4 * review_directed``.
- Louvain communities and betweenness centrality are stored per node.

This module only regroups those stored values for display:
- one undirected *pair* per developer pair: co-edit score + review A->B + review B->A, whose
  ``strength`` equals the sum of the two directional edge weights (the same value as the
  existing "top pairs" list), so no stored score changes meaning;
- node selection by the union of commit, PR-participation and collaboration rankings;
- project-wide statistics kept apart from the statistics of the displayed subset.

Counts that the original calculation did not store (co-edited files, shared PRs, review counts,
PR lists) come from ``network/pair-details``. Snapshots made before it existed simply lack them
(``details_available`` is False) and the screen shows them as unavailable instead of zero.
"""
from __future__ import annotations

import math

HUB_SHARE = 0.15                    # top 15% betweenness across the whole project
STRENGTH_QUANTILES = {"no_weak": 0.30, "medium": 0.50, "strong": 0.70}
CO_EDIT_SHARE, REVIEW_SHARE = 0.6, 0.4
PAIR_PR_LIMIT = 10
CONTRIBUTOR_PR_LIMIT = 20
LARGE_PR_RULE = "변경 파일이 max(15, ⌈기준 SHA 파일 수 × 0.1⌉)개 이상인 PR은 공동 수정 계산에서 제외"


def _key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _quantile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank quantile of an ascending list (None when empty)."""
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, math.ceil(q * len(sorted_values)) - 1))
    return sorted_values[index]


def project_pairs(p: dict) -> dict[tuple[int, int], dict]:
    """Undirected pairs rebuilt from the stored directional edges (unrounded values)."""
    pairs: dict[tuple[int, int], dict] = {}
    for e in p["network"]["edges"]:
        a, b = e["source"], e["target"]
        key = _key(a, b)
        entry = pairs.setdefault(key, {"a": key[0], "b": key[1], "co_edit_raw": 0.0,
                                       "review_a_to_b": 0.0, "review_b_to_a": 0.0, "strength": 0.0})
        entry["co_edit_raw"] = max(entry["co_edit_raw"], e.get("co_edit_raw") or 0.0)
        review = e.get("review_directed") or 0.0
        if a == key[0]:
            entry["review_a_to_b"] = review
        else:
            entry["review_b_to_a"] = review
        entry["strength"] += e.get("edge_weight") or 0.0
    details = {(d["a"], d["b"]): d for d in (p.get("network/pair-details") or {}).get("pairs", [])}
    for key, entry in pairs.items():
        entry["co_edit_score"] = CO_EDIT_SHARE * entry["co_edit_raw"]
        entry["review_a_to_b_score"] = REVIEW_SHARE * entry["review_a_to_b"]
        entry["review_b_to_a_score"] = REVIEW_SHARE * entry["review_b_to_a"]
        entry["co_edit"] = entry["co_edit_raw"] > 0
        entry["review"] = entry["review_a_to_b"] > 0 or entry["review_b_to_a"] > 0
        d = details.get(key)
        entry["co_edit_files"] = d["co_edit_files"] if d else None
        entry["co_pr_count"] = d["co_pr_count"] if d else None
        entry["reviews_a_to_b"] = d["reviews_a_to_b"] if d else None
        entry["reviews_b_to_a"] = d["reviews_b_to_a"] if d else None
        entry["shared_pr_count"] = d["shared_pr_count"] if d else None
        entry["prs"] = (d["prs"][:PAIR_PR_LIMIT] if d else None)
    return pairs


def _pr_participation(p: dict) -> dict[int, dict] | None:
    details = p.get("network/pair-details")
    if not details:
        return None
    return {int(cid): v for cid, v in details.get("contributors", {}).items()}


def network_view(p: dict, limit: int = 50, min_edge_weight: float = 0.0) -> dict:
    net = p["network"]
    all_nodes = net["nodes"]
    names = {n["id"]: n["name"] for n in all_nodes}
    pairs = project_pairs(p)
    participation = _pr_participation(p)
    pr_counts = {c["id"]: c.get("pr_count") for c in p.get("top-contributors", {}).get("contributors", [])}

    degree: dict[int, int] = {n["id"]: 0 for n in all_nodes}
    strength: dict[int, float] = {n["id"]: 0.0 for n in all_nodes}
    for (a, b), pair in pairs.items():
        for x in (a, b):
            degree[x] = degree.get(x, 0) + 1
            strength[x] = strength.get(x, 0.0) + pair["strength"]

    total = len(all_nodes)
    ranked_betweenness = sorted((n.get("betweenness_centrality") or 0.0 for n in all_nodes), reverse=True)
    hub_threshold = ranked_betweenness[max(1, math.ceil(HUB_SHARE * total)) - 1] if total else None

    community_size: dict[int, int] = {}
    for n in all_nodes:
        if n.get("cluster") is not None and degree.get(n["id"], 0) > 0:
            community_size[n["cluster"]] = community_size.get(n["cluster"], 0) + 1
    community_order = sorted(community_size, key=lambda c: (-community_size[c], c))
    community_rank = {c: i for i, c in enumerate(community_order)}

    def node_record(n: dict) -> dict:
        nid = n["id"]
        betweenness = n.get("betweenness_centrality") or 0.0
        part = participation.get(nid) if participation is not None else None
        pr_count = pr_counts.get(nid)
        if pr_count is None and part is not None:
            pr_count = part.get("prs_authored", 0)
        reviews_given = part.get("reviews_given", 0) if part is not None else (0 if participation is not None else None)
        cluster = n.get("cluster") if degree.get(nid, 0) > 0 else None
        return {
            "id": nid, "name": n["name"], "commit_count": n.get("commit_count") or 0,
            "pr_count": pr_count, "reviews_given": reviews_given,
            "degree": degree.get(nid, 0),
            "degree_centrality": round(degree.get(nid, 0) / (total - 1), 4) if total > 1 else 0.0,
            "collaboration_strength": round(strength.get(nid, 0.0), 6),
            "betweenness_centrality": betweenness,
            "is_hub": bool(hub_threshold and betweenness > 0 and betweenness >= hub_threshold),
            "cluster": cluster, "community_size": community_size.get(cluster) if cluster is not None else None,
            "community_rank": community_rank.get(cluster) if cluster is not None else None,
            "isolated": degree.get(nid, 0) == 0,
            # compatibility: the stored Louvain id, also for isolated (singleton) developers
            "louvain_cluster": n.get("cluster"),
        }

    records = {n["id"]: node_record(n) for n in all_nodes}
    connected = [r for r in records.values() if not r["isolated"]]

    # Node selection: union of three rankings, picked in turn so each criterion is represented.
    rankings: dict[str, list[dict]] = {
        "commits": sorted(connected, key=lambda r: (-r["commit_count"], r["id"])),
        "collaboration": sorted(connected, key=lambda r: (-r["collaboration_strength"], -r["degree"], r["id"])),
    }
    pr_available = any(r["pr_count"] is not None for r in connected) or participation is not None
    if pr_available:
        def pr_score(r: dict) -> int:
            return (r["pr_count"] or 0) + (r["reviews_given"] or 0)
        rankings["prs"] = sorted(connected, key=lambda r: (-pr_score(r), -r["commit_count"], r["id"]))
    order = [c for c in ("commits", "prs", "collaboration") if c in rankings]
    chosen: list[int] = []
    seen: set[int] = set()
    cursors = {c: 0 for c in order}
    while len(chosen) < min(limit, len(connected)):
        progressed = False
        for c in order:
            ranking = rankings[c]
            while cursors[c] < len(ranking) and ranking[cursors[c]]["id"] in seen:
                cursors[c] += 1
            if cursors[c] < len(ranking) and len(chosen) < limit:
                nid = ranking[cursors[c]]["id"]
                chosen.append(nid); seen.add(nid); cursors[c] += 1; progressed = True
        if not progressed:
            break
    top_by = {c: {r["id"] for r in rankings[c][:limit]} for c in order}
    nodes = []
    for nid in chosen:
        r = dict(records[nid])
        r["selected_by"] = [c for c in order if nid in top_by[c]]
        nodes.append(r)
    shown = set(chosen)

    visible_pairs = []
    for (a, b), pair in sorted(pairs.items(), key=lambda kv: (-kv[1]["strength"], kv[0])):
        if a in shown and b in shown and pair["strength"] >= min_edge_weight:
            visible_pairs.append({**pair, "a_name": names.get(a, "익명"), "b_name": names.get(b, "익명")})

    strengths = sorted(pair["strength"] for pair in pairs.values())
    cutoffs = {name: _quantile(strengths, q) for name, q in STRENGTH_QUANTILES.items()}

    with_pair = {x for pair in visible_pairs for x in (pair["a"], pair["b"])}
    view_communities = {r["cluster"] for r in nodes if r["cluster"] is not None}
    isolated = sorted((r for r in records.values() if r["isolated"]), key=lambda r: (-r["commit_count"], r["id"]))
    communities = [{"id": c, "size": community_size[c], "rank": community_rank[c],
                    "shown": sum(1 for r in nodes if r["cluster"] == c)} for c in community_order]

    # Directional edges among displayed nodes, kept for older screens.
    compat_edges = [e for e in net["edges"]
                    if e["source"] in shown and e["target"] in shown and e["edge_weight"] >= min_edge_weight]

    return {
        "nodes": nodes,
        "edges": compat_edges,
        "pairs": visible_pairs,
        "metrics": net["metrics"],
        "project": {
            "total_developers": total,
            "total_commits": (p.get("summary") or {}).get("total_commits"),
            "total_prs": (p.get("data_quality") or {}).get("merged_pr_count"),
            "total_relations": len(pairs),
            "community_count": len(community_size),
            "isolated_count": len(isolated),
            "density": round(2 * len(pairs) / (total * (total - 1)), 4) if total > 1 else 0.0,
            "hub_count": sum(1 for r in records.values() if r["is_hub"]),
        },
        "view": {
            "developer_count": len(nodes),
            "relation_count": len(visible_pairs),
            "community_count": len(view_communities),
            "isolated_count": sum(1 for r in nodes if r["id"] not in with_pair),
        },
        "selection": {
            "limit": limit, "criteria": order, "connected_developers": len(connected),
            "pr_participation_available": pr_available,
        },
        "communities": communities,
        "isolated": [{"id": r["id"], "name": r["name"], "commit_count": r["commit_count"], "pr_count": r["pr_count"]}
                     for r in isolated[:500]],
        "strength_cutoffs": cutoffs,
        "hub_rule": {"share": HUB_SHARE, "threshold": hub_threshold},
        "details_available": p.get("network/pair-details") is not None,
        "review_available": any(pair["review"] for pair in pairs.values()),
        "large_pr_rule": LARGE_PR_RULE,
    }


def contributor_collaboration(p: dict, contributor_id: int, limit: int = 20) -> dict:
    """Per-developer collaborators with their counts, and the PRs shared with anyone (details only)."""
    pairs = project_pairs(p)
    names = {n["id"]: n["name"] for n in p["network"]["nodes"]}
    mine = []
    shared: dict[int, dict] = {}
    for (a, b), pair in pairs.items():
        if contributor_id not in (a, b):
            continue
        other = b if a == contributor_id else a
        out_score = pair["review_a_to_b_score"] if a == contributor_id else pair["review_b_to_a_score"]
        in_score = pair["review_b_to_a_score"] if a == contributor_id else pair["review_a_to_b_score"]
        out_count = pair["reviews_a_to_b"] if a == contributor_id else pair["reviews_b_to_a"]
        in_count = pair["reviews_b_to_a"] if a == contributor_id else pair["reviews_a_to_b"]
        mine.append({"contributor": {"id": other, "name": names.get(other, "익명")}, "strength": pair["strength"],
                     "co_edit_score": pair["co_edit_score"], "review_out_score": out_score, "review_in_score": in_score,
                     "co_edit_files": pair["co_edit_files"], "co_pr_count": pair["co_pr_count"],
                     "reviews_out": out_count, "reviews_in": in_count})
        for pr in pair["prs"] or []:
            shared.setdefault(pr["number"], pr)
    mine.sort(key=lambda c: (-c["strength"], c["contributor"]["id"]))
    prs = sorted(shared.values(), key=lambda pr: (pr.get("merged_at") or "", pr["number"]), reverse=True)
    return {"collaborator_count": len(mine), "collaborators_detail": mine[:limit],
            "shared_prs": prs[:CONTRIBUTOR_PR_LIMIT] if p.get("network/pair-details") is not None else None}
