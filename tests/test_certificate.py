"""最小可追溯证书测试：最少保留数、字典序 tie-break、逐端区间相等。"""

import itertools
import random
import unittest

from app.solver import certificate, compute_bounds, solve, ValidationError


def make_records(spec):
    return [
        {"id": "R%d" % (i + 1), "a": a, "d": d, "lo": lo, "hi": hi}
        for i, (a, d, lo, hi) in enumerate(spec)
    ]


class CertificateTests(unittest.TestCase):
    def test_drops_redundant_record(self):
        # README 示例：R3（B 相对基准 [-1,3]）由 R1+R2 链式蕴含
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "B"}]
        records = make_records([
            ("A", "REF", -1, 1),
            ("B", "A", 0, 2),
            ("B", "REF", -1, 3),
        ])
        full = solve(probes, records, 0)
        self.assertTrue(full.feasible)
        cert = certificate(probes, records, 0)
        self.assertTrue(cert.feasible)
        self.assertEqual(cert.kept_indices, (0, 1))
        self.assertEqual(cert.cert_bounds, cert.full_bounds)
        # 保留记录独立求解，逐探头逐端等于全量
        sub = [records[i] for i in cert.kept_indices]
        self.assertEqual(compute_bounds(probes, sub, 0),
                         compute_bounds(probes, records, 0))

    def test_all_records_necessary(self):
        # 三条记录各绑定一个不同的区间端点，缺一不可
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "B"}]
        records = make_records([
            ("A", "REF", -1, 1),   # A 的上下界
            ("B", "A", -2, 4),     # B 的下界经 A 链绑定为 -3
            ("B", "REF", -5, 2),   # B 的上界绑定为 2
        ])
        cert = certificate(probes, records, 0)
        self.assertTrue(cert.feasible)
        self.assertEqual(cert.kept_indices, (0, 1, 2))
        self.assertEqual(cert.full_bounds,
                         ((0, 0), (-1, 1), (-3, 2)))
        # 任意删掉一条，区间都会变
        for drop in range(3):
            sub = [r for i, r in enumerate(records) if i != drop]
            self.assertNotEqual(compute_bounds(probes, sub, 0),
                                cert.full_bounds)

    def test_lexicographic_tie_break(self):
        # R1 与 R2 完全等价（重复记录），最小保留数同为 2 时须取编号更小组合
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "B"}]
        records = make_records([
            ("A", "REF", -1, 1),
            ("A", "REF", -1, 1),
            ("B", "REF", 0, 2),
        ])
        cert = certificate(probes, records, 0)
        self.assertTrue(cert.feasible)
        self.assertEqual(cert.kept_indices, (0, 2))

    def test_infeasible_model_rejected(self):
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "B"}]
        records = make_records([
            ("A", "REF", 1, 2),
            ("REF", "B", 1, 2),
            ("B", "A", 1, 2),
        ])
        cert = certificate(probes, records, 0)
        self.assertFalse(cert.feasible)

    def test_disconnected_rejected(self):
        # 求解器层：与基准不连通时全量区间无界，不产生证书
        probes = [{"id": "REF"}, {"id": "A"}, {"id": "X"}]
        records = make_records([
            ("A", "REF", 0, 1),
            ("A", "REF", 0, 1),
            ("A", "REF", 0, 1),
        ])
        self.assertFalse(certificate(probes, records, 0).feasible)
        # 服务层语义不变：solve 仍以 ValidationError 明确拒绝（422）
        with self.assertRaises(ValidationError):
            solve(probes, records, 0)

    def test_bruteforce_minimality_and_order_random(self):
        """随机实例上对照全枚举：保留数最少，同数取字典序最小下标序列。"""
        random.seed(2026)
        for trial in range(120):
            n = random.randint(3, 5)
            pids = ["B"] + ["P%d" % i for i in range(n - 1)]
            probes = [{"id": p} for p in pids]
            m = random.randint(n - 1, 8)
            pairs = [(pids[i], pids[j])
                     for i in range(n) for j in range(n) if i != j]
            records = []
            for t in range(m):
                a, d = random.choice(pairs)
                lo = random.randint(-4, 3)
                hi = lo + random.randint(0, 5)
                records.append({"id": "R%d" % (t + 1), "a": a, "d": d,
                                "lo": lo, "hi": hi})
            full = compute_bounds(probes, records, 0)
            if full is None:
                self.assertFalse(certificate(probes, records, 0).feasible)
                continue
            cert = certificate(probes, records, 0)
            self.assertTrue(cert.feasible, trial)
            kept = list(cert.kept_indices)
            self.assertEqual(sorted(kept), kept)
            self.assertEqual(
                compute_bounds(probes, [records[i] for i in kept], 0),
                full, trial)
            # 不存在更少记录的合格子集
            for k in range(len(kept)):
                for combo in itertools.combinations(range(m), k):
                    self.assertNotEqual(
                        compute_bounds(
                            probes, [records[i] for i in combo], 0),
                        full, (trial, combo))
            # 同数下不存在字典序更小的合格子集
            for combo in itertools.combinations(range(m), len(kept)):
                if list(combo) >= kept:
                    break
                self.assertNotEqual(
                    compute_bounds(
                        probes, [records[i] for i in combo], 0),
                    full, (trial, combo, kept))


if __name__ == "__main__":
    unittest.main()
