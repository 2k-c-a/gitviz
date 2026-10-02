"""분석 회귀 테스트: 운영 DB 없이 실제 계산 함수와 SQL을 실행한다.

python scripts/test_analytics_calculations.py
DB 초기화가 있는 모듈 import를 피하기 위해 해당 함수 정의만 AST로 로드한다.
NetworkX는 프로젝트 requirements의 설치본을 사용한다.
"""
import ast
import math
import sqlite3
import unittest
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / "app/api/analytics.py").read_text(encoding="utf-8"))
names = {"_ownership_analysis", "_fetch_network_edges", "_compute_network_metrics"}
namespace = {"math": math, "nx": nx, "text": lambda sql: sql}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name in names], type_ignores=[]), str(ROOT / "app/api/analytics.py"), "exec"), namespace)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def scalar(self):
        return self.cursor.fetchone()[0]

    def mappings(self):
        return self

    def all(self):
        return [dict(row) for row in self.cursor.fetchall()]


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE pull_requests (id INTEGER, repository_id INTEGER, is_large_pr BOOLEAN, author_contributor_id INTEGER);
            CREATE TABLE pull_request_files (pull_request_id INTEGER, file_id INTEGER);
            CREATE TABLE pr_commits (pull_request_id INTEGER, commit_id INTEGER);
            CREATE TABLE commits (id INTEGER, repository_id INTEGER, contributor_id INTEGER);
            CREATE TABLE commit_file_changes (commit_id INTEGER, file_id INTEGER);
            CREATE TABLE pr_reviews (pull_request_id INTEGER, reviewer_contributor_id INTEGER);
            INSERT INTO pull_requests VALUES (1, 1, false, 1);
            INSERT INTO pull_request_files VALUES (1, 10), (1, 20);
            INSERT INTO pr_commits VALUES (1, 100), (1, 200), (1, 300);
            INSERT INTO commits VALUES (100, 1, 1), (200, 1, 2), (300, 1, 1);
            INSERT INTO commit_file_changes VALUES (100, 10), (200, 20), (300, 10);
        """)

    def execute(self, sql, params):
        return Result(self.conn.execute(sql, params))


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()

    def tearDown(self):
        self.db.conn.close()

    def test_different_files_in_same_pr_are_not_co_edit(self):
        result = namespace["_fetch_network_edges"](self.db, 1)
        self.assertEqual(result["edges"], [])

    def test_shared_file_counted_once_per_author_pair(self):
        self.db.conn.execute("INSERT INTO commit_file_changes VALUES (200, 10)")
        edges = namespace["_fetch_network_edges"](self.db, 1)["edges"]
        self.assertEqual(len(edges), 2)
        self.assertEqual({(e["source"], e["target"]) for e in edges}, {(1, 2), (2, 1)})
        self.assertTrue(all(e["co_edit_raw"] == 1.0 for e in edges))
        self.assertTrue(all(math.isclose(e["edge_weight"], 0.3) for e in edges))

    def test_unlinked_commit_does_not_invent_co_edit(self):
        self.db.conn.execute("INSERT INTO commits VALUES (400, 1, 3)")
        self.db.conn.execute("INSERT INTO commit_file_changes VALUES (400, 10)")
        self.assertEqual(namespace["_fetch_network_edges"](self.db, 1)["edges"], [])

    def test_review_direction_and_self_review(self):
        self.db.conn.executescript("INSERT INTO pr_reviews VALUES (1, 2), (1, 1);")
        edges = namespace["_fetch_network_edges"](self.db, 1)["edges"]
        self.assertEqual(len(edges), 1)
        self.assertEqual((edges[0]["source"], edges[0]["target"]), (2, 1))

    def test_small_positive_edge_survives(self):
        self.db.conn.execute("INSERT INTO pr_reviews VALUES (1, 2)")
        self.db.conn.executemany("INSERT INTO pr_reviews VALUES (1, 1)", [()] * 10000)
        edge = namespace["_fetch_network_edges"](self.db, 1)["edges"][0]
        self.assertGreater(edge["edge_weight"], 0)
        self.assertLess(edge["edge_weight"], 0.00005)

    def test_strong_route_is_shorter_than_weak_route(self):
        edges = [{"source": a, "target": b, "edge_weight": w}
                 for a, b, w in [(1, 2, 10), (2, 4, 10), (1, 3, 1), (3, 4, 1)]]
        metrics = namespace["_compute_network_metrics"](edges, [1, 2, 3, 4, 5])
        self.assertGreater(metrics["betweenness"][2], metrics["betweenness"][3])
        self.assertEqual(metrics["isolated_contributor_count"], 1)

    def test_nonpositive_edges_do_not_connect_nodes(self):
        edges = [{"source": 1, "target": 2, "edge_weight": w} for w in [0, -1]]
        metrics = namespace["_compute_network_metrics"](edges, [1, 2])
        self.assertEqual(metrics["isolated_contributor_count"], 2)
        self.assertEqual(metrics["network_density"], 0)

    def test_ownership_exact_eighty_percent(self):
        owners = [{"contributor_id": i, "name": str(i), "cnt": count}
                  for i, count in enumerate([7, 1, 1, 1])]
        self.assertEqual(namespace["_ownership_analysis"](owners)["bus_factor"], 2)
        owners[0]["cnt"] = 8
        owners.pop()
        self.assertEqual(namespace["_ownership_analysis"](owners)["bus_factor"], 1)

    def test_empty_ownership_is_unknown(self):
        self.assertIsNone(namespace["_ownership_analysis"]([])["bus_factor"])


if __name__ == "__main__":
    unittest.main()
