"""Collaboration Network view (app/services/collab_network.py) on synthetic snapshot payloads.

No database: payloads are built here in the stored snapshot shape (directional edges with
edge_weight = 0.6 * co_edit_raw / 2 + 0.4 * review_directed, Louvain cluster and betweenness per node).
"""
import os
import sys
import unittest
from pathlib import Path

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.collab_network import contributor_collaboration, network_view  # noqa: E402


def payload(people, co_edit=(), reviews=(), clusters=None, betweenness=None, details=True, prs=None):
    """people: {id: commits}; co_edit: [(a, b, raw)]; reviews: [(reviewer, author, raw)]."""
    raw = {}
    for a, b, v in co_edit:
        raw.setdefault((a, b), {'co': 0.0, 'r': 0.0})['co'] = v
        raw.setdefault((b, a), {'co': 0.0, 'r': 0.0})['co'] = v
    for a, b, v in reviews:
        raw.setdefault((a, b), {'co': 0.0, 'r': 0.0})['r'] = v
    edges = [{'source': a, 'target': b, 'co_edit_raw': v['co'], 'review_directed': v['r'],
              'edge_weight': 0.6 * v['co'] / 2 + 0.4 * v['r']} for (a, b), v in raw.items()]
    clusters = clusters or {}
    betweenness = betweenness or {}
    nodes = [{'id': i, 'name': f'dev-{i}', 'commit_count': c, 'cluster': clusters.get(i, i),
              'betweenness_centrality': betweenness.get(i, 0.0)} for i, c in people.items()]
    p = {
        'network': {'nodes': nodes, 'edges': edges, 'metrics': {'total_contributor_count': len(nodes)}},
        'top-contributors': {'contributors': [{'id': i, 'commit_count': c, **({'pr_count': (prs or {}).get(i, 0)} if details else {})}
                                              for i, c in people.items() if c]},
        'summary': {'total_commits': sum(people.values())},
        'data_quality': {'merged_pr_count': sum((prs or {}).values())},
    }
    if details:
        pairs = {}
        for a, b, _ in co_edit:
            k = (min(a, b), max(a, b))
            pairs.setdefault(k, {'a': k[0], 'b': k[1], 'co_edit_files': 0, 'co_pr_count': 0, 'reviews_a_to_b': 0,
                                 'reviews_b_to_a': 0, 'shared_pr_count': 0, 'prs': []})
            pairs[k].update(co_edit_files=3, co_pr_count=2, shared_pr_count=2,
                            prs=[{'number': 10 + a + b, 'title': 'shared', 'merged_at': '2026-10-01T00:00:00'}])
        for a, b, _ in reviews:
            k = (min(a, b), max(a, b))
            entry = pairs.setdefault(k, {'a': k[0], 'b': k[1], 'co_edit_files': 0, 'co_pr_count': 0, 'reviews_a_to_b': 0,
                                         'reviews_b_to_a': 0, 'shared_pr_count': 1, 'prs': []})
            entry['reviews_a_to_b' if a == k[0] else 'reviews_b_to_a'] += 4
        p['network/pair-details'] = {'pairs': list(pairs.values()), 'contributors': {
            str(i): {'prs_authored': (prs or {}).get(i, 0), 'reviews_given': sum(1 for r, _, _ in reviews if r == i)} for i in people}}
    return p


def ring(n, start=1):
    return [(i, i + 1, 0.1) for i in range(start, start + n - 1)]


class CollabNetworkViewTests(unittest.TestCase):
    def test_co_edit_is_one_undirected_pair_and_review_keeps_direction(self):
        p = payload({1: 5, 2: 3, 3: 1}, co_edit=[(1, 2, 0.5)], reviews=[(3, 1, 0.25)], clusters={1: 0, 2: 0, 3: 0})
        view = network_view(p)
        self.assertEqual(len(view['pairs']), 2)
        co = next(x for x in view['pairs'] if {x['a'], x['b']} == {1, 2})
        self.assertTrue(co['co_edit']); self.assertFalse(co['review'])
        self.assertAlmostEqual(co['strength'], 0.3)          # 0.15 + 0.15: same as the stored A->B + B->A sum
        self.assertAlmostEqual(co['co_edit_score'], 0.3)
        rv = next(x for x in view['pairs'] if {x['a'], x['b']} == {1, 3})
        self.assertFalse(rv['co_edit']); self.assertTrue(rv['review'])
        self.assertEqual((rv['a'], rv['b'], rv['review_a_to_b'], rv['review_b_to_a']), (1, 3, 0.0, 0.25))   # 3 reviewed 1
        self.assertEqual((rv['reviews_a_to_b'], rv['reviews_b_to_a']), (0, 4))
        self.assertEqual(len(view['edges']), 3)               # stored directional edges stay available

    def test_small_project_five_developers(self):
        p = payload({1: 40, 2: 20, 3: 10, 4: 5, 5: 2}, co_edit=ring(5), clusters={i: 0 for i in range(1, 6)},
                    betweenness={2: 0.5, 3: 0.6, 4: 0.5}, prs={1: 4, 2: 2})
        view = network_view(p, limit=50)
        self.assertEqual(len(view['nodes']), 5)
        self.assertEqual(view['project']['total_relations'], 4)
        self.assertEqual(view['project']['community_count'], 1)
        self.assertEqual([n['id'] for n in view['nodes'] if n['is_hub']], [3])     # top 15% of 5 = 1 developer
        self.assertEqual(view['view'], {'developer_count': 5, 'relation_count': 4, 'community_count': 1, 'isolated_count': 0})

    def test_twenty_developers_two_communities_and_union_selection(self):
        people = {i: 100 - i for i in range(1, 21)}
        people[20] = 1                                     # few commits, but the strongest collaborator
        co = ring(10, 1) + ring(10, 11) + [(20, 1, 0.9), (20, 5, 0.9), (20, 15, 0.9)]
        clusters = {i: (0 if i <= 10 else 1) for i in range(1, 21)}
        view = network_view(payload(people, co_edit=co, clusters=clusters, prs={20: 9}), limit=5)
        ids = [n['id'] for n in view['nodes']]
        self.assertEqual(len(ids), 5)
        self.assertIn(20, ids)                             # kept by PR participation / collaboration, not commits
        self.assertIn('collaboration', next(n for n in view['nodes'] if n['id'] == 20)['selected_by'])
        self.assertEqual(view['project']['total_developers'], 20)
        self.assertEqual(view['view']['developer_count'], 5)
        self.assertEqual([c['size'] for c in view['communities']], [10, 10])

    def test_hundred_plus_developers_limit_and_project_stats_apart(self):
        people = {i: (i % 17) + 1 for i in range(1, 131)}
        co = ring(60, 1) + ring(60, 61)
        clusters = {i: i // 20 for i in people}
        view = network_view(payload(people, co_edit=co, clusters=clusters), limit=50)
        self.assertEqual(len(view['nodes']), 50)
        self.assertEqual(view['project']['total_developers'], 130)
        self.assertEqual(view['project']['isolated_count'], 10)     # 121..130 have no relation
        self.assertTrue(all(not n['isolated'] for n in view['nodes']))
        self.assertTrue(all(p['a'] in {n['id'] for n in view['nodes']} for p in view['pairs']))
        self.assertLessEqual(view['view']['relation_count'], view['project']['total_relations'])

    def test_no_pull_requests_and_direct_push_project(self):
        view = network_view(payload({1: 50, 2: 30, 3: 10}, prs={}))
        self.assertEqual((view['nodes'], view['pairs'], view['edges']), ([], [], []))
        self.assertEqual(view['project']['isolated_count'], 3)
        self.assertEqual([x['id'] for x in view['isolated']], [1, 2, 3])
        self.assertEqual(view['project']['community_count'], 0)
        self.assertEqual(view['strength_cutoffs'], {'no_weak': None, 'medium': None, 'strong': None})

    def test_no_review_data_creates_no_review_relation(self):
        view = network_view(payload({1: 5, 2: 5, 3: 5}, co_edit=[(1, 2, 0.4)]))
        self.assertFalse(view['review_available'])
        self.assertFalse(any(x['review'] for x in view['pairs']))
        self.assertTrue(all((x['reviews_a_to_b'], x['reviews_b_to_a']) == (0, 0) for x in view['pairs']))

    def test_many_isolated_developers_are_listed_not_drawn(self):
        people = {i: 3 for i in range(1, 41)}
        view = network_view(payload(people, co_edit=[(1, 2, 0.2), (2, 3, 0.2)], clusters={1: 0, 2: 0, 3: 0}))
        self.assertEqual({n['id'] for n in view['nodes']}, {1, 2, 3})
        self.assertEqual(len(view['isolated']), 37)
        self.assertTrue(all(x['commit_count'] == 3 for x in view['isolated']))

    def test_commits_concentrated_on_one_developer(self):
        people = {1: 5000, 2: 3, 3: 2, 4: 1}
        view = network_view(payload(people, co_edit=[(1, 2, 0.1), (2, 3, 0.1), (3, 4, 0.1)], clusters={i: 0 for i in people}))
        self.assertEqual(len(view['nodes']), 4)
        self.assertEqual(view['nodes'][0]['id'], 1)

    def test_strength_cutoffs_follow_the_project_distribution(self):
        co = [(1, 2, 0.1), (2, 3, 0.2), (3, 4, 0.3), (4, 5, 0.4), (5, 6, 0.5), (6, 7, 0.6), (7, 8, 0.7), (8, 9, 0.8), (9, 10, 0.9), (10, 1, 1.0)]
        view = network_view(payload({i: 1 for i in range(1, 11)}, co_edit=co, clusters={i: 0 for i in range(1, 11)}))
        c = view['strength_cutoffs']
        self.assertAlmostEqual(c['no_weak'], 0.18); self.assertAlmostEqual(c['medium'], 0.30); self.assertAlmostEqual(c['strong'], 0.42)   # strengths 0.06..0.60, nearest rank
        self.assertEqual(sum(1 for x in view['pairs'] if x['strength'] >= c['strong']), 4)   # top ~30% (ties kept)

    def test_old_snapshot_without_details_keeps_working(self):
        p = payload({1: 5, 2: 4}, co_edit=[(1, 2, 0.3)], details=False)
        view = network_view(p)
        self.assertFalse(view['details_available'])
        self.assertIsNone(view['pairs'][0]['co_pr_count'])
        self.assertIsNone(view['nodes'][0]['pr_count'])
        self.assertEqual(view['selection']['criteria'], ['commits', 'collaboration'])
        self.assertIsNone(contributor_collaboration(p, 1)['shared_prs'])

    def test_min_edge_weight_hides_relations_but_keeps_developers(self):
        p = payload({1: 5, 2: 4, 3: 3}, co_edit=[(1, 2, 0.9), (2, 3, 0.05)], clusters={1: 0, 2: 0, 3: 0})
        view = network_view(p, min_edge_weight=0.1)
        self.assertEqual(len(view['nodes']), 3)
        self.assertEqual(len(view['pairs']), 1)
        self.assertEqual(view['view']['isolated_count'], 1)            # 3 has no visible relation in this view
        self.assertEqual(view['project']['isolated_count'], 0)

    def test_contributor_collaboration_counts_and_shared_prs(self):
        p = payload({1: 5, 2: 4, 3: 3}, co_edit=[(1, 2, 0.5)], reviews=[(3, 1, 0.5)], clusters={1: 0, 2: 0, 3: 0})
        detail = contributor_collaboration(p, 1)
        self.assertEqual(detail['collaborator_count'], 2)
        by = {c['contributor']['id']: c for c in detail['collaborators_detail']}
        self.assertEqual((by[2]['co_pr_count'], by[2]['co_edit_files']), (2, 3))
        self.assertEqual((by[3]['reviews_in'], by[3]['reviews_out']), (4, 0))
        self.assertEqual([pr['number'] for pr in detail['shared_prs']], [13])

    def test_no_collaboration_at_all(self):
        view = network_view(payload({1: 1}))
        self.assertEqual(view['project']['density'], 0.0)
        self.assertEqual(view['view']['developer_count'], 0)
        self.assertEqual(view['hub_rule']['threshold'], 0.0)
        self.assertFalse(any(n['is_hub'] for n in view['nodes']))


if __name__ == '__main__':
    unittest.main()
