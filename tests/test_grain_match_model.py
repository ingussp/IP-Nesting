"""Run with python -m unittest discover -s tests -p 'test_*.py'."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from IPNestingGrainMatchModel import polygon, solve, pack_groups, expand_result, transform


def cabinet(quantity=1):
    parts = [dict(id='part_%d' % i, points=[[0, 0], [100, 0], [100, h], [0, h]],
                  quantity=quantity, rotations=4, holes=[],
                  _ip_nesting=dict(preview_object_name='Front%d' % i, source_part_index=i, job_id='test'))
             for i, h in enumerate([200, 40, 40, 40])]
    definition = dict(names=['Front%d' % i for i in range(4)],
                      outlines=[polygon(p['points']) for p in parts], links=[[2, 8], [6, 12], [10, 16]])
    return parts, definition


class GrainModelTests(unittest.TestCase):
    def test_four_fronts_follow_spacing(self):
        parts, definition = cabinet()
        poses = solve(parts, definition['links'], 12)
        for actual, expected in zip(poses, [[0, 156, 0], [0, 104, 0], [0, 52, 0], [0, 0, 0]]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b)
        packed, recipes = pack_groups(parts, [definition], 6, lambda *args: None)
        self.assertAlmostEqual(packed[0]['points'][2][1], 338)
        self.assertEqual(len(recipes[0]['members']), 4)

    def test_engine_sees_only_perimeter(self):
        parts, definition = cabinet(2)
        packed, recipes = pack_groups(parts, [definition], 12, lambda *args: None)
        self.assertEqual(len(packed), 1)
        self.assertEqual(packed[0]['quantity'], 2)
        self.assertEqual(packed[0]['allowedAngles'], [0, 90, 180, 270])
        for private in ('grain', 'members', 'pose', 'Front'):
            self.assertNotIn(private, json.dumps(packed))
        self.assertEqual(recipes[0]['id'], packed[0]['id'])

    def test_invalid_or_stale_group_is_rejected(self):
        parts, definition = cabinet()
        for links in ([[2, 8]], [[2, 8], [2, 12], [10, 16]], [[2, 8], [6, 12], [9, 3]]):
            with self.assertRaises(ValueError):
                solve(parts, links, 12)
        for change in ('quantity', 'geometry', 'missing'):
            changed = copy.deepcopy(parts)
            if change == 'quantity': changed[0]['quantity'] = 2
            if change == 'geometry': changed[0]['points'][1][0] += 1
            if change == 'missing': changed.pop()
            with self.assertRaises(ValueError):
                pack_groups(changed, [definition], 12, lambda *args: None)

    def test_rotation_rules_intersect(self):
        parts, definition = cabinet()
        parts[1]['allowedAngles'] = [0, 180]
        packed, _ = pack_groups(parts, [definition], 12, lambda *args: None)
        self.assertEqual(packed[0]['allowedAngles'], [0, 180])
        parts[0]['allowedAngles'] = [90, 270]
        with self.assertRaises(ValueError):
            pack_groups(parts, [definition], 12, lambda *args: None)

    def test_rotated_result_expands_without_changing_raw_result(self):
        parts, definition = cabinet()
        packed, recipes = pack_groups(parts, [definition], 12, lambda *args: None)
        raw = dict(placed=1, sheets=[dict(points=[[0, 0], [500, 0], [500, 500], [0, 500]], parts=[
            dict(id=1, source=packed[0]['id'], x=400, y=20, rotation=90)])], unplaced=[])
        session = dict(grain_matching_proxies=recipes,
                       parts=[dict(source_part_index=i, part_id=p['id']) for i, p in enumerate(parts)])
        result = expand_result(raw, session)
        self.assertEqual(result['placed'], 4)
        self.assertEqual(raw['placed'], 1)
        self.assertAlmostEqual(result['sheets'][0]['parts'][0]['x'], 244)
        self.assertAlmostEqual(result['sheets'][0]['parts'][0]['y'], 20)
        self.assertAlmostEqual(result['utilisation'], 12.8)
        for actual, expected in zip(result['sheets'][0]['parts'][0]['points'],
                                    transform(parts[0]['points'], [244, 20, 90])):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b)
        raw['sheets'] = []
        raw['unplaced'] = [dict(id=2, source=packed[0]['id'])]
        result = expand_result(raw, session)
        self.assertEqual(result['unplacedCount'], 4)
        self.assertEqual(result['placed'], 0)

    def test_ordinary_job_is_unchanged(self):
        parts, _ = cabinet()
        self.assertEqual(pack_groups(parts, [], 12, lambda *args: None), (parts, []))
        raw = dict(placed=3, sheets=[])
        self.assertEqual(expand_result(raw, {}), raw)


if __name__ == '__main__':
    unittest.main()
