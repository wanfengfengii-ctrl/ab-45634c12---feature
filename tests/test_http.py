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

    # ---------- 最小可追溯证书 ----------

    CERT_PAYLOAD = {
        "probes": ["REF", "A", "B"],
        "base_probe": "REF",
        "records": [
            {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
            {"id": "R2", "a": "B", "d": "A", "lo": 0, "hi": 2},
            {"id": "R3", "a": "B", "d": "REF", "lo": -1, "hi": 3},
        ],
    }

    def test_certificate_success(self):
        code, data = self._post(
            "/api/calibrations/certificate", self.CERT_PAYLOAD)
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "certified")
        self.assertEqual([r["id"] for r in data["retained_records"]],
                         ["R1", "R2"])
        self.assertEqual([r["id"] for r in data["omitted_records"]], ["R3"])
        self.assertEqual(data["retained_count"], 2)
        self.assertEqual(data["omitted_count"], 1)
        for pid in ("REF", "A", "B"):
            rg = data["ranges"][pid]
            self.assertTrue(rg["min_equal"])
            self.assertTrue(rg["max_equal"])
            self.assertEqual(rg["full"], rg["certificate"])
        # 与全量 solve 的区间逐端一致
        _, solve_data = self._post(
            "/api/calibrations/solve", self.CERT_PAYLOAD)
        for pid in ("REF", "A", "B"):
            self.assertEqual(data["ranges"][pid]["full"],
                             solve_data["ranges"][pid])
        # 全量指纹与证书稳定指纹
        self.assertEqual(data["input_fingerprint"],
                         solve_data["input_fingerprint"])
        self.assertEqual(len(data["certificate_fingerprint"]), 16)
        self.assertNotEqual(data["certificate_fingerprint"],
                            data["input_fingerprint"])

    def test_certificate_all_necessary(self):
        payload = {
            "probes": ["REF", "A", "B"],
            "base_probe": "REF",
            "records": [
                {"id": "R1", "a": "A", "d": "REF", "lo": -1, "hi": 1},
                {"id": "R2", "a": "B", "d": "A", "lo": -2, "hi": 4},
                {"id": "R3", "a": "B", "d": "REF", "lo": -5, "hi": 2},
            ],
        }
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 200)
        self.assertEqual(data["retained_count"], 3)
        self.assertEqual(data["omitted_count"], 0)
        self.assertEqual([r["id"] for r in data["retained_records"]],
                         ["R1", "R2", "R3"])
        self.assertEqual(data["omitted_records"], [])

    def test_certificate_rejects_infeasible_409(self):
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
        self.assertNotIn("retained_records", data)
        self.assertLess(data["contradiction_cycle"]["upper_bound_sum"], 0)

    def test_certificate_rejects_disconnected_422(self):
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

    def test_certificate_rejects_bad_fields_422(self):
        base = json.loads(json.dumps(self.CERT_PAYLOAD))
        # 下界 > 上界
        bad = json.loads(json.dumps(base))
        bad["records"][0]["hi"] = -9
        code, data = self._post("/api/calibrations/certificate", bad)
        self.assertEqual(code, 422)
        self.assertEqual(data["status"], "invalid_input")
        # 探头越界（11 支）
        bad = {"probes": ["P%d" % i for i in range(11)],
               "base_probe": "P0",
               "records": [
                   {"id": "R%d" % i, "a": "P0", "d": "P%d" % ((i % 10) + 1),
                    "lo": 0, "hi": 1} for i in range(3)]}
        code, data = self._post("/api/calibrations/certificate", bad)
        self.assertEqual(code, 422)
        # 记录少于 3
        bad = json.loads(json.dumps(base))
        bad["records"] = bad["records"][:2]
        code, data = self._post("/api/calibrations/certificate", bad)
        self.assertEqual(code, 422)

    def test_certificate_limit_is_16_while_solve_allows_24(self):
        # 17 条记录：solve 接受（≤24），certificate 拒绝（>16）
        payload = {
            "probes": ["REF", "A", "B"],
            "base_probe": "REF",
            "records": (
                [{"id": "R1", "a": "A", "d": "REF", "lo": -5, "hi": 5}] +
                [{"id": "R%d" % i, "a": "B", "d": "A",
                  "lo": -5, "hi": 5} for i in range(2, 10)] +
                [{"id": "R%d" % i, "a": "B", "d": "REF",
                  "lo": -5, "hi": 5} for i in range(10, 19)]
            ),
        }
        self.assertEqual(len(payload["records"]), 18)
        code, _ = self._post("/api/calibrations/solve", payload)
        self.assertEqual(code, 200)
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 422)
        self.assertIn("16", data["error"])

    def test_certificate_exactly_16_records_ok(self):
        records = [
            {"id": "R1", "a": "A", "d": "REF", "lo": -5, "hi": 5},
            {"id": "R16", "a": "B", "d": "REF", "lo": -5, "hi": 5},
        ]
        for i in range(2, 16):
            records.append({"id": "R%d" % i, "a": "B", "d": "A",
                            "lo": -5, "hi": 5})
        payload = {"probes": ["REF", "A", "B"], "base_probe": "REF",
                   "records": records}
        code, data = self._post(
            "/api/calibrations/certificate", payload)
        self.assertEqual(code, 200, data)
        self.assertEqual(data["status"], "certified")

    def test_certificate_unknown_route_404(self):
        code, _ = self._post("/api/calibrations/nope", self.CERT_PAYLOAD)
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
