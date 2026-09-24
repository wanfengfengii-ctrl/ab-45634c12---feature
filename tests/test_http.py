"""HTTP 层测试：直接在本机随机端口起服务，用 urllib 走真实 HTTP。"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from app.server import create_server


class HttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def _post(self, path, payload):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_health_and_page(self):
        with urllib.request.urlopen(self._url("/healthz"), timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read())["status"], "ok")
        with urllib.request.urlopen(self._url("/"), timeout=5) as resp:
            body = resp.read().decode("utf-8")
            self.assertIn("热电偶", body)

    def test_feasible_flow(self):
        payload = {
            "probes": ["REF", "A", "B"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
                {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
                {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
            ],
        }
        code, data = self._post("/api/calibrations/solve", payload)
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "feasible")
        self.assertEqual(data["ranges"]["REF"], {"min": 0, "max": 0, "tight": True})
        self.assertEqual(data["ranges"]["A"], {"min": -1, "max": 1, "tight": False})
        self.assertEqual(data["ranges"]["B"], {"min": -1, "max": 3, "tight": False})
        for r in payload["records"]:
            diff = data["witness"][r["a"]] - data["witness"][r["d"]]
            self.assertTrue(r["lo"] <= diff <= r["hi"])
        self.assertEqual(data["witness"]["REF"], 0)

    def test_record_change_invalidates_old_result_via_fingerprint(self):
        base = {
            "probes": ["REF", "A", "B"], "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
                {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
                {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
            ],
        }
        _, d1 = self._post("/api/calibrations/solve", base)
        changed = json.loads(json.dumps(base))
        changed["records"][0]["hi"] = 1  # unchanged value -> same fingerprint
        _, d2 = self._post("/api/calibrations/solve", changed)
        self.assertEqual(d1["input_fingerprint"], d2["input_fingerprint"])

        changed["records"][0]["hi"] = 0
        _, d3 = self._post("/api/calibrations/solve", changed)
        self.assertNotEqual(d1["input_fingerprint"], d3["input_fingerprint"])

    def test_infeasible_returns_cycle(self):
        payload = {
            "probes": ["REF", "A", "B"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": 1, "hi": 2},
                {"id": "R2", "a": "REF", "d": "B", "lo": 1, "hi": 2},
                {"id": "R3", "a": "B", "d": "A", "lo": 1, "hi": 2},
            ],
        }
        code, data = self._post("/api/calibrations/solve", payload)
        self.assertEqual(code, 409)
        self.assertEqual(data["status"], "infeasible")
        self.assertTrue(data["rejected"])
        cyc = data["contradiction_cycle"]
        self.assertLess(cyc["upper_bound_sum"], 0)
        self.assertEqual(
            sum(s["upper_bound"] for s in cyc["steps"]),
            cyc["upper_bound_sum"])
        self.assertTrue(all(s["direction"] in ("forward", "reverse")
                            for s in cyc["steps"]))

    def test_validation_errors(self):
        bad_cases = [
            {"probes": ["A", "B"], "base_probe": "A", "records": [
                {"id": "R1", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1}]},
            {"probes": ["A", "B", "C"], "base_probe": "X", "records": [
                {"id": "R1", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1}]},
            {"probes": ["A", "B", "C"], "base_probe": "A", "records": [
                {"id": "R1", "a": "A", "d": "B", "lo": 2, "hi": 1},
                {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1}]},
            {"probes": ["A", "A", "C"], "base_probe": "A", "records": [
                {"id": "R1", "a": "A", "d": "C", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "C", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "C", "lo": 0, "hi": 1}]},
            {"probes": ["A", "B", "C"], "base_probe": "A", "records": [
                {"id": "R1", "a": "B", "d": "C", "lo": 0, "hi": 1},
                {"id": "R2", "a": "B", "d": "C", "lo": 0, "hi": 1}]},
        ]
        for payload in bad_cases:
            code, data = self._post("/api/calibrations/solve", payload)
            self.assertEqual(code, 422, payload)
            self.assertEqual(data["status"], "invalid_input")

    def test_disconnected_is_422(self):
        payload = {
            "probes": ["REF", "A", "X"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "REF", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "REF", "lo": 0, "hi": 1},
            ],
        }
        code, data = self._post("/api/calibrations/solve", payload)
        self.assertEqual(code, 422)
        self.assertIn("X", data["error"])

    def test_malformed_json(self):
        req = urllib.request.Request(
            self._url("/api/calibrations/solve"),
            data=b"{not json", headers={"Content-Type": "application/json"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    CERT_PAYLOAD = {
        "probes": ["REF", "A", "B"],
        "base_probe": "REF",
        "records": [
            {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
            {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
        ],
    }

    def test_certificate_success_structure(self):
        code, data = self._post(
            "/api/calibrations/certificate", self.CERT_PAYLOAD)
        self.assertEqual(code, 201, data)
        self.assertEqual(data["status"], "certificate")
        self.assertEqual(data["retained_record_ids"], ["R1", "R2"])
        self.assertEqual(data["omitted_record_ids"], ["R3"])
        self.assertEqual(data["retained_count"], 2)
        self.assertEqual(data["omitted_count"], 1)
        # 保留与省略清单完整列出原始记录
        self.assertEqual([r["id"] for r in data["retained_records"]], ["R1", "R2"])
        self.assertEqual([r["id"] for r in data["omitted_records"]], ["R3"])
        # 每支探头：全量与证书逐端相等
        for pr in data["probe_ranges"]:
            self.assertTrue(pr["min_equal"], pr)
            self.assertTrue(pr["max_equal"], pr)
            self.assertEqual(pr["full"], pr["certificate"])
        self.assertEqual(data["ranges"]["B"],
                         {"min": -1, "max": 3, "tight": False})
        self.assertEqual(data["witness"]["REF"], 0)
        # 稳定指纹齐全且为十六进制串
        self.assertRegex(data["input_fingerprint"], r"^[0-9a-f]{16}$")
        self.assertRegex(data["certificate_fingerprint"], r"^[0-9a-f]{16}$")
        self.assertEqual(data["selection"]["method"],
                         "exhaustive_subset_enumeration")
        self.assertGreaterEqual(data["selection"]["subsets_evaluated"], 1)

    def test_certificate_input_fingerprint_matches_solve(self):
        _, solve_data = self._post(
            "/api/calibrations/solve", self.CERT_PAYLOAD)
        _, cert_data = self._post(
            "/api/calibrations/certificate", self.CERT_PAYLOAD)
        self.assertEqual(cert_data["input_fingerprint"],
                         solve_data["input_fingerprint"])
        # 同一模型重复生成，证书指纹稳定
        _, cert_data2 = self._post(
            "/api/calibrations/certificate", self.CERT_PAYLOAD)
        self.assertEqual(cert_data["certificate_fingerprint"],
                         cert_data2["certificate_fingerprint"])

    def test_certificate_changes_when_record_edited(self):
        _, c1 = self._post(
            "/api/calibrations/certificate", self.CERT_PAYLOAD)
        changed = json.loads(json.dumps(self.CERT_PAYLOAD))
        changed["records"][0]["hi"] = 0  # R1 上界 1 -> 0
        _, c2 = self._post("/api/calibrations/certificate", changed)
        self.assertEqual(c2["status"], "certificate")
        self.assertNotEqual(c1["input_fingerprint"], c2["input_fingerprint"])
        self.assertNotEqual(c1["certificate_fingerprint"],
                            c2["certificate_fingerprint"])

    def test_certificate_rejects_infeasible_with_409(self):
        payload = {
            "probes": ["REF", "A", "B"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": 1, "hi": 2},
                {"id": "R2", "a": "REF", "d": "B", "lo": 1, "hi": 2},
                {"id": "R3", "a": "B", "d": "A", "lo": 1, "hi": 2},
            ],
        }
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 409)
        self.assertEqual(data["status"], "infeasible")
        self.assertTrue(data["rejected"])
        self.assertNotIn("certificate_fingerprint", data)
        self.assertLess(data["contradiction_cycle"]["upper_bound_sum"], 0)

    def test_certificate_rejects_disconnected_with_422(self):
        payload = {
            "probes": ["REF", "A", "X"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "REF", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "REF", "lo": 0, "hi": 1},
            ],
        }
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 422)
        self.assertEqual(data["status"], "invalid_input")
        self.assertIn("X", data["error"])

    def test_certificate_rejects_invalid_fields_422(self):
        # 探头数越界
        code, data = self._post("/api/calibrations/certificate", {
            "probes": ["A", "B"], "base_probe": "A",
            "records": [
                {"id": "R1", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1}]})
        self.assertEqual(code, 422)
        # lo > hi
        code, data = self._post("/api/calibrations/certificate", {
            "probes": ["A", "B", "C"], "base_probe": "A",
            "records": [
                {"id": "R1", "a": "B", "d": "C", "lo": 9, "hi": 1},
                {"id": "R2", "a": "A", "d": "B", "lo": 0, "hi": 1},
                {"id": "R3", "a": "A", "d": "B", "lo": 0, "hi": 1}]})
        self.assertEqual(code, 422)
        self.assertEqual(data["status"], "invalid_input")

    def test_certificate_limit_sixteen_records(self):
        records = [
            {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
            {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
        ]
        # 16 条：证书接口接受（追加不改变可行域的重复记录）
        for i in range(13):
            records.append({"id": "X%02d" % i, "a": "A", "d": "REF",
                            "lo": -10, "hi": 10})
        payload = {"probes": ["REF", "A", "B"], "base_probe": "REF",
                   "records": records}
        code, data = self._post("/api/calibrations/certificate", payload)
        self.assertEqual(code, 201, data)

        # 17 条：证书接口 422，但求解接口（上限 24）仍然照常 200，
        # 证明原有求解接口语义与限制未被改变
        records.append({"id": "X99", "a": "A", "d": "REF",
                        "lo": -10, "hi": 10})
        payload17 = {"probes": ["REF", "A", "B"], "base_probe": "REF",
                     "records": records}
        code, data = self._post("/api/calibrations/certificate", payload17)
        self.assertEqual(code, 422)
        self.assertIn("16", data["error"])
        code, data = self._post("/api/calibrations/solve", payload17)
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "feasible")

    def test_certificate_retained_subset_independently_equivalent(self):
        # 选一个最少保留数恰为 3 的模型（三条记录分别负责不同的端）
        payload = {
            "probes": ["REF", "A", "C"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
                {"id": "R2", "a": "C", "d": "A", "lo": 0, "hi": 2},
                {"id": "R3", "a": "C", "d": "REF", "lo": 1, "hi": 5},
            ],
        }
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 201, data)
        self.assertEqual(data["retained_count"], 3)
        sub_payload = {
            "probes": payload["probes"],
            "base_probe": payload["base_probe"],
            "records": data["retained_records"],
        }
        _, sub = self._post("/api/calibrations/solve", sub_payload)
        _, full = self._post("/api/calibrations/solve", payload)
        self.assertEqual(sub["ranges"], full["ranges"])


if __name__ == "__main__":
    unittest.main()
