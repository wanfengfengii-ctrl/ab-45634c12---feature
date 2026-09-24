"""求解器单元测试：紧区间、见证解、矛盾闭环、不连通。"""

import itertools
import random
import unittest

from app.solver import solve, ValidationError


def all_assignments(probes, lo, hi):
    ids = [p["id"] for p in probes]
    nonbase = [i for i in ids if i != "B"]
    for vals in itertools.product(range(lo, hi + 1), repeat=len(nonbase)):
        d = {"B": 0}
        d.update(dict(zip(nonbase, vals)))
        yield d


def feasible_assign(d, records):
    return all(r["lo"] <= d[r["a"]] - d[r["d"]] <= r["hi"] for r in records)


class SolverFeasibleTests(unittest.TestCase):
    def test_chain_ranges_and_witness(self):
        probes = [{"id": "B"}, {"id": "A"}, {"id": "C"}]
        records = [
            {"id": "R1", "a": "A", "d": "B", "lo": -1, "hi": 1},
            {"id": "R2", "a": "A", "d": "C", "lo": 2, "hi": 2},
        ]
        r = solve(probes, records, 0)
        self.assertTrue(r.feasible)
        self.assertEqual(r.ranges["B"], {"min": 0, "max": 0, "tight": True})
        self.assertEqual(r.ranges["A"], {"min": -1, "max": 1, "tight": False})
        self.assertEqual(r.ranges["C"], {"min": -3, "max": -1, "tight": False})
        self.assertTrue(feasible_assign(r.witness, records))
        self.assertEqual(r.witness["B"], 0)

    def test_tight_equality(self):
        probes = [{"id": "B"}, {"id": "X"}]
        records = [
            {"id": "R1", "a": "X", "d": "B", "lo": 5, "hi": 5},
            {"id": "R2", "a": "B", "d": "X", "lo": -5, "hi": -5},
            {"id": "R3", "a": "X", "d": "B", "lo": 5, "hi": 5},
        ]
        r = solve(probes, records, 0)
        self.assertTrue(r.feasible)
        self.assertEqual(r.ranges["X"], {"min": 5, "max": 5, "tight": True})
        self.assertEqual(r.witness["X"], 5)

    def test_ranges_are_tight_bruteforce(self):
        """用穷举验证 min/max 真可达，且任何可行解都落在区间内。"""
        probes = [{"id": "B"}, {"id": "P"}, {"id": "Q"}]
        cases = [
            [("P", "B", -2, 1), ("Q", "P", 0, 2), ("Q", "B", -1, 2)],
            [("P", "B", -3, 3), ("Q", "P", -2, 2), ("Q", "B", -4, 0)],
            [("P", "Q", -1, 1), ("Q", "B", 1, 3), ("P", "B", 0, 4)],
        ]
        for k, cs in enumerate(cases):
            records = [{"id": "R%d" % (i + 1), "a": a, "d": d, "lo": lo, "hi": hi}
                       for i, (a, d, lo, hi) in enumerate(cs)]
            r = solve(probes, records, 0)
            self.assertTrue(r.feasible, k)
            mins = {p: 10**9 for p in ("P", "Q")}
            maxs = {p: -10**9 for p in ("P", "Q")}
            count = 0
            for d in all_assignments(probes, -6, 6):
                if feasible_assign(d, records):
                    count += 1
                    for p in ("P", "Q"):
                        mins[p] = min(mins[p], d[p])
                        maxs[p] = max(maxs[p], d[p])
                        self.assertGreaterEqual(d[p], r.ranges[p]["min"], (k, p, d))
                        self.assertLessEqual(d[p], r.ranges[p]["max"], (k, p, d))
            self.assertGreater(count, 0)
            for p in ("P", "Q"):
                self.assertEqual(r.ranges[p]["min"], mins[p], (k, p))
                self.assertEqual(r.ranges[p]["max"], maxs[p], (k, p))


class InfeasibleTests(unittest.TestCase):
    def test_simple_negative_cycle(self):
        probes = [{"id": "B"}, {"id": "A"}, {"id": "C"}]
        records = [
            {"id": "R1", "a": "A", "d": "B", "lo": 1, "hi": 2},
            {"id": "R2", "a": "B", "d": "C", "lo": 1, "hi": 2},
            {"id": "R3", "a": "C", "d": "A", "lo": 1, "hi": 2},
        ]
        r = solve(probes, records, 0)
        self.assertFalse(r.feasible)
        desc = r.cycle.describe(records)
        self.assertLess(desc["upper_bound_sum"], 0)
        # 闭环步引用的都是真实记录编号
        self.assertTrue({s["record_id"] for s in desc["steps"]} <= {"R1", "R2", "R3"})
        self.assertIn(sum(s["upper_bound"] for s in desc["steps"]),
                      [desc["upper_bound_sum"]])

    def test_upper_bound_cycle(self):
        # 上界环矛盾：x[A]-x[B]<=2, x[B]-x[C]<=2, x[C]-x[A]<=-5
        probes = [{"id": "B"}, {"id": "A"}, {"id": "C"}]
        records = [
            {"id": "R1", "a": "A", "d": "B", "lo": -100, "hi": 2},
            {"id": "R2", "a": "B", "d": "C", "lo": -100, "hi": 2},
            {"id": "R3", "a": "C", "d": "A", "lo": -100, "hi": -5},
        ]
        r = solve(probes, records, 0)
        self.assertFalse(r.feasible)
        self.assertLess(r.cycle.total, 0)
        used = {e.record_index for e in r.cycle.edges}
        self.assertEqual(used, {0, 1, 2})

    def test_disconnected_rejected(self):
        probes = [{"id": "B"}, {"id": "A"}, {"id": "X"}]
        records = [
            {"id": "R1", "a": "A", "d": "B", "lo": 0, "hi": 1},
            {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
            {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1},
        ]
        with self.assertRaises(ValidationError):
            solve(probes, records, 0)


class FuzzTests(unittest.TestCase):
    def test_random_against_bruteforce(self):
        random.seed(42)
        probes = [{"id": "B"}, {"id": "P"}, {"id": "Q"}]
        for _ in range(120):
            # 生成随机记录；若碰巧与基准不连通（属另一类拒绝情形）则重抽
            names = [("P", "B"), ("B", "P"), ("Q", "B"), ("B", "Q"),
                     ("P", "Q"), ("Q", "P")]
            while True:
                records = []
                for i in range(3):
                    a, d = random.choice(names)
                    lo = random.randint(-3, 2)
                    hi = lo + random.randint(0, 4)
                    records.append({"id": "R%d" % (i + 1), "a": a, "d": d,
                                    "lo": lo, "hi": hi})
                try:
                    r = solve(probes, records, 0)
                    break
                except ValidationError:
                    continue

            feasible_set = [d for d in all_assignments(probes, -7, 7)
                            if feasible_assign(d, records)]
            if feasible_set:
                self.assertTrue(r.feasible)
                self.assertTrue(feasible_assign(r.witness, records))
                for p in ("P", "Q"):
                    lo_e = min(d[p] for d in feasible_set)
                    hi_e = max(d[p] for d in feasible_set)
                    self.assertEqual(r.ranges[p]["min"], lo_e)
                    self.assertEqual(r.ranges[p]["max"], hi_e)
                    for d in feasible_set:
                        self.assertGreaterEqual(d[p], r.ranges[p]["min"])
                        self.assertLessEqual(d[p], r.ranges[p]["max"])
            else:
                self.assertFalse(r.feasible)
                self.assertLess(r.cycle.total, 0)


if __name__ == "__main__":
    unittest.main()
