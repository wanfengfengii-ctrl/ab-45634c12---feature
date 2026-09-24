"""最小可追溯证书：穷举最小保留集、字典序决胜、逐端区间复现。"""

import itertools
import random
import unittest

from app.certificate import build_certificate, find_minimal_retention
from app.solver import solve


def brute_force_best(probes, records, base_index, full):
    """参考实现：枚举全部子集，按规则（先数量、再编号升序字典序）取最优。"""
    ids = [r["id"] for r in records]
    full_ranges = full.ranges
    best = None
    for mask in range(1, 1 << len(records)):
        sub = [records[i] for i in range(len(records)) if mask & (1 << i)]
        try:
            got = solve(probes, sub, base_index)
        except Exception:
            continue
        if not got.feasible:
            continue
        if any(got.ranges[p["id"]] != full_ranges[p["id"]] for p in probes):
            continue
        seq = tuple(sorted(r["id"] for r in sub))
        cand = (len(seq), seq)
        if best is None or cand < best:
            best = cand
    return best


class MinimalRetentionTests(unittest.TestCase):
    def test_drops_single_redundant_record(self):
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "B"}]
        records = [
            {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
            # R3 完全被 R1+R2 蕴含：B 相对 REF ∈ [-1, 3]
            {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
        ]
        full = solve(probes, records, 0)
        self.assertTrue(full.feasible)
        kept, _ = find_minimal_retention(probes, records, 0, full.ranges)
        self.assertEqual([records[i]["id"] for i in kept], ["R1", "R2"])

        cert = build_certificate(probes, records, 0, full)
        self.assertEqual(cert["retained_record_ids"], ["R1", "R2"])
        self.assertEqual(cert["omitted_record_ids"], ["R3"])
        for pr in cert["probe_ranges"]:
            self.assertTrue(pr["min_equal"] and pr["max_equal"])
            self.assertEqual(pr["full"], pr["certificate"])

    def test_lexicographic_tiebreak_on_equal_size(self):
        # 多组同数子集都能复现全量区间，必须取编号升序序列字典序最小者
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "C"}]
        records = [
            {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R2", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R3", "a": "C", "d": "A", "lo": 0, "hi": 2},
            {"id": "R4", "a": "C", "d": "REF", "lo": -1, "hi": 3},
        ]
        full = solve(probes, records, 0)
        cert = build_certificate(probes, records, 0, full)
        # 可行二元组：(R1,R3) (R1,R4) (R2,R3) (R2,R4)，字典序最小为 R1,R3
        self.assertEqual(cert["retained_count"], 2)
        self.assertEqual(cert["retained_record_ids"], ["R1", "R3"])
        self.assertEqual(set(cert["omitted_record_ids"]), {"R2", "R4"})

    def test_string_id_ordering_not_numeric(self):
        # 编号按字符串字典序："R10" < "R2"
        probes = [{"id": "B"}, {"id": "A"}]
        records = [
            {"id": "R10", "a": "A", "d": "B", "lo": 0, "hi": 2},
            {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 2},
            {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 2},
        ]
        full = solve(probes, records, 0)
        kept, _ = find_minimal_retention(probes, records, 0, full.ranges)
        self.assertEqual([records[i]["id"] for i in kept], ["R10"])

    def test_certificate_never_loosens_or_tightens(self):
        probes = [{"id": "B"}, {"id": "P"}, {"id": "Q"}]
        records = [
            {"id": "R1", "a": "P", "d": "B", "lo": -2, "hi": 1},
            {"id": "R2", "a": "Q", "d": "P", "lo": 0, "hi": 2},
            {"id": "R3", "a": "Q", "d": "B", "lo": -1, "hi": 2},
            {"id": "R4", "a": "P", "d": "Q", "lo": -3, "hi": 1},
        ]
        full = solve(probes, records, 0)
        cert = build_certificate(probes, records, 0, full)
        retained = cert["retained_records"]
        sub = solve(probes, retained, 0)
        for p in probes:
            self.assertEqual(sub.ranges[p["id"]], full.ranges[p["id"]])
        # 见证解满足全部保留记录
        for r in retained:
            diff = sub.witness[r["a"]] - sub.witness[r["d"]]
            self.assertTrue(r["lo"] <= diff <= r["hi"])

    def test_all_records_required(self):
        # R3 收紧 C 的下界；任删一条区间都会变或探头失去基准连通
        probes = [{"id": "B"}, {"id": "A"}, {"id": "C"}]
        records = [
            {"id": "R1", "a": "A", "d": "B", "lo": -1, "hi": 1},
            {"id": "R2", "a": "C", "d": "A", "lo": 0, "hi": 2},
            {"id": "R3", "a": "C", "d": "B", "lo": 1, "hi": 5},  # 收紧 C 下界
        ]
        full = solve(probes, records, 0)
        self.assertTrue(full.feasible)
        self.assertEqual(full.ranges["C"], {"min": 1, "max": 3, "tight": False})
        cert = build_certificate(probes, records, 0, full)
        self.assertEqual(cert["retained_count"], 3)
        self.assertEqual(cert["omitted_count"], 0)


class ExhaustiveCrossCheckTests(unittest.TestCase):
    def test_random_matches_bruteforce_reference(self):
        random.seed(7)
        names = [("P", "B"), ("B", "P"), ("Q", "B"), ("B", "Q"),
                 ("P", "Q"), ("Q", "P")]
        probes = [{"id": "B"}, {"id": "P"}, {"id": "Q"}]
        for trial in range(80):
            records = []
            for i in range(random.randint(3, 6)):
                a, d = random.choice(names)
                lo = random.randint(-3, 2)
                hi = lo + random.randint(0, 4)
                records.append({"id": "R%02d" % (i + 1), "a": a, "d": d,
                                "lo": lo, "hi": hi})
            try:
                full = solve(probes, records, 0)
            except Exception:
                continue  # 碰巧与基准不连通，换一批
            if not full.feasible:
                continue
            kept, _ = find_minimal_retention(probes, records, 0, full.ranges)
            got = (len(kept), tuple(sorted(records[i]["id"] for i in kept)))
            expect = brute_force_best(probes, records, 0, full)
            self.assertEqual(got, expect, trial)

    def test_retained_independent_ranges_for_all_subset_sizes(self):
        """对一批小模型穷举：证书保留集独立求解必须逐端等于全量。"""
        probes = [{"id": "B"}, {"id": "P"}, {"id": "Q"}]
        base_cases = [
            [("P", "B", -1, 1), ("Q", "P", 0, 2), ("Q", "B", -1, 3)],
            [("P", "B", -2, 1), ("Q", "P", -1, 2), ("Q", "B", 0, 2),
             ("P", "Q", -2, 1)],
        ]
        for cs in base_cases:
            records = [{"id": "R%d" % (i + 1), "a": a, "d": d, "lo": lo, "hi": hi}
                       for i, (a, d, lo, hi) in enumerate(cs)]
            full = solve(probes, records, 0)
            cert = build_certificate(probes, records, 0, full)
            sub = solve(probes, cert["retained_records"], 0)
            for p in probes:
                self.assertEqual(sub.ranges[p["id"]], full.ranges[p["id"]])
            self.assertLessEqual(cert["retained_count"], len(records))


if __name__ == "__main__":
    unittest.main()
