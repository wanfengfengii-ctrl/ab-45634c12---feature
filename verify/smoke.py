"""一次性验收：代码测试 + 构建检查 + 业务 HTTP 冒烟，结束后以退出码报告。

在 verify 容器中运行：
  1. py_compile 全量语法构建检查
  2. unittest 代码测试（求解器 + HTTP 层）
  3. 等待 web 服务健康后做业务 HTTP 冒烟：
       - GET /healthz、GET /
       - 可行联立：校验紧确区间、见证解、基准为 0
       - 最小可追溯证书：200 certified，保留/省略记录、全量与证书逐端相等、
         稳定指纹；仅用保留记录独立求解区间不变
       - 改动任一记录后 input_fingerprint 必须变化（旧结论/证书撤下）
       - 不可行联立：HTTP 409 + 矛盾闭环（证书接口同样拒绝）
       - 非法输入（含证书接口 >16 条）：HTTP 422
"""

import json
import os
import py_compile
import sys
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE_URL = os.environ.get("BASE_URL", "http://web:8000")

failures = []


def report(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s %s" % (mark, name, ("- " + detail) if detail else ""),
          flush=True)
    if not ok:
        failures.append(name)


def build_check():
    print("== 1. 构建检查（py_compile） ==", flush=True)
    ok = True
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
        for f in files:
            if f.endswith(".py"):
                try:
                    py_compile.compile(os.path.join(dirpath, f), doraise=True)
                except py_compile.PyCompileError as exc:
                    ok = False
                    print(exc)
    report("语法构建检查", ok)


def unit_tests():
    print("== 2. 代码测试（unittest） ==", flush=True)
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    result = runner.run(suite)
    report("单元/HTTP 测试套件", result.wasSuccessful(),
          "运行 %d 个，失败 %d，错误 %d"
          % (result.testsRun, len(result.failures), len(result.errors)))


def _request(method, path, body=None, timeout=5):
    url = BASE_URL + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


FEASIBLE_PAYLOAD = {
    "probes": ["REF", "TC-A", "TC-B", "TC-C"],
    "base_probe": "REF",
    "records": [
        {"id": "R1", "a": "TC-A", "d": "REF",  "lo": -1, "hi": 1},
        {"id": "R2", "a": "TC-B", "d": "TC-A", "lo": 0,  "hi": 2},
        {"id": "R3", "a": "TC-C", "d": "TC-B", "lo": -3, "hi": 3},
        {"id": "R4", "a": "TC-C", "d": "REF",  "lo": -2, "hi": 0},
        # R5 对 TC-B 给出的 [-1,3] 已被 R1+R2 链式蕴含，属可省略冗余记录
        {"id": "R5", "a": "TC-B", "d": "REF",  "lo": -1, "hi": 3},
    ],
}

INFEASIBLE_PAYLOAD = {
    "probes": ["REF", "TC-A", "TC-B"],
    "base_probe": "REF",
    "records": [
        {"id": "R1", "a": "TC-A", "d": "REF",  "lo": 1, "hi": 2},
        {"id": "R2", "a": "REF",  "d": "TC-B", "lo": 1, "hi": 2},
        {"id": "R3", "a": "TC-B", "d": "TC-A", "lo": 1, "hi": 2},
    ],
}


def wait_healthy(deadline_s=45):
    print("== 3. 业务 HTTP 冒烟（目标 %s） ==" % BASE_URL, flush=True)
    deadline = time.time() + deadline_s
    last_err = ""
    while time.time() < deadline:
        try:
            code, data = _request("GET", "/healthz")
            if code == 200 and data.get("status") == "ok":
                report("健康检查 /healthz", True)
                return True
            last_err = "code=%s body=%s" % (code, data)
        except Exception as exc:  # noqa: BLE001 - 验收脚本需兜底所有连接异常
            last_err = repr(exc)
        time.sleep(1)
    report("健康检查 /healthz", False, last_err)
    return False


def smoke():
    # 页面是 HTML，单独抓原文，不走 JSON 解析
    try:
        with urllib.request.urlopen(BASE_URL + "/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        page_ok = resp.status == 200 and "热电偶" in html and \
            "/api/calibrations/solve" in html and \
            "/api/calibrations/certificate" in html
        detail = ""
    except Exception as exc:  # noqa: BLE001
        page_ok = False
        detail = repr(exc)
    report("录入页面 GET /", page_ok, detail)

    # --- 可行联立 ---
    code, data = _request("POST", "/api/calibrations/solve", FEASIBLE_PAYLOAD)
    ok = (code == 200 and data.get("status") == "feasible")
    report("可行联立返回 200 feasible", ok, "code=%s" % code)

    if ok:
        ranges = data["ranges"]
        witness = data["witness"]
        expected = {
            "REF":  (0, 0),
            "TC-A": (-1, 1),
        }
        range_ok = all(ranges[p]["min"] == lo and ranges[p]["max"] == hi
                       for p, (lo, hi) in expected.items())
        report("紧确闭区间正确（联立而非逐条中值）", range_ok,
               json.dumps(ranges, ensure_ascii=False))
        report("基准校正值固定为 0", witness["REF"] == 0)

        witness_ok = all(
            r["lo"] <= witness[r["a"]] - witness[r["d"]] <= r["hi"]
            for r in FEASIBLE_PAYLOAD["records"]
        )
        report("见证校正值满足全部比对记录", witness_ok,
               json.dumps(witness, ensure_ascii=False))

        fp0 = data["input_fingerprint"]

        # 改动任一记录：旧结论必须撤下（指纹变化）
        changed = json.loads(json.dumps(FEASIBLE_PAYLOAD))
        changed["records"][2]["lo"] = -2  # R3 下界 -3 -> -2
        code2, d2 = _request("POST", "/api/calibrations/solve", changed)
        report("记录改动后重新求解", code2 == 200 and d2.get("status") == "feasible")
        report("改动后旧结论撤下（指纹变化）",
               d2.get("input_fingerprint") != fp0,
               "%s -> %s" % (fp0, d2.get("input_fingerprint")))

        # --- 最小可追溯证书（提交当前完整可行模型） ---
        code, cd = _request("POST", "/api/calibrations/certificate",
                            FEASIBLE_PAYLOAD)
        cert_ok = (code == 200 and cd.get("status") == "certified")
        report("证书生成返回 200 certified", cert_ok,
               "code=%s status=%s" % (code, cd.get("status")))
        if cert_ok:
            kept = [r["id"] for r in cd.get("retained_records", [])]
            dropped = [r["id"] for r in cd.get("omitted_records", [])]
            all_ids = {r["id"] for r in FEASIBLE_PAYLOAD["records"]}
            partition_ok = (
                set(kept).isdisjoint(dropped)
                and set(kept) | set(dropped) == all_ids
                and cd.get("retained_count") == len(kept)
                and cd.get("omitted_count") == len(dropped)
            )
            report("证书列出保留与省略的原始记录且互斥完备",
                   partition_ok, "保留=%s 省略=%s" % (kept, dropped))

            eq_ok = all(
                v.get("min_equal") and v.get("max_equal")
                and v.get("full") == v.get("certificate")
                for v in cd.get("ranges", {}).values()
            )
            # 与全量 solve 的区间逐端一致
            eq_ok = eq_ok and all(
                cd["ranges"][p]["full"] == ranges[p] for p in ranges)
            report("每支探头证书区间与全量逐端相等", eq_ok,
                   json.dumps(cd.get("ranges"), ensure_ascii=False))

            fp_ok = (isinstance(cd.get("certificate_fingerprint"), str)
                     and len(cd["certificate_fingerprint"]) == 16
                     and cd.get("input_fingerprint") == fp0)
            report("证书含稳定指纹并与全量指纹并存", fp_ok,
                   "full=%s cert=%s"
                   % (cd.get("input_fingerprint"),
                      cd.get("certificate_fingerprint")))

            # 独立性复核：仅用保留记录再走 solve，区间必须完全一致
            kept_records = [r for r in FEASIBLE_PAYLOAD["records"]
                            if r["id"] in set(kept)]
            sub_payload = {
                "probes": FEASIBLE_PAYLOAD["probes"],
                "base_probe": FEASIBLE_PAYLOAD["base_probe"],
                "records": kept_records,
            }
            c3, d3 = _request("POST", "/api/calibrations/solve", sub_payload)
            indep_ok = c3 == 200 and all(
                d3["ranges"][p] == ranges[p] for p in ranges)
            report("仅保留记录独立求解区间与全量相同（非逐条删除凑出）",
                   indep_ok, json.dumps(d3.get("ranges"),
                                        ensure_ascii=False) if c3 == 200
                   else "code=%s" % c3)

    # 证书接口对不可行模型必须沿用 409 拒绝，且不产生证书
    code, cd = _request("POST", "/api/calibrations/certificate",
                        INFEASIBLE_PAYLOAD)
    report("不可行模型证书接口明确拒绝（409，无证书）",
           code == 409 and cd.get("status") == "infeasible"
           and cd.get("rejected") is True
           and "retained_records" not in cd
           and cd.get("contradiction_cycle", {}).get("upper_bound_sum", 0) < 0,
           "code=%s" % code)

    # 证书接口至多 16 条记录：17 条须 422
    too_many = {
        "probes": ["REF", "TC-A", "TC-B"], "base_probe": "REF",
        "records": [
            {"id": "R%d" % i,
             "a": "TC-A" if i % 2 else "TC-B",
             "d": "REF", "lo": -5, "hi": 5}
            for i in range(1, 18)],
    }
    code, cd = _request("POST", "/api/calibrations/certificate", too_many)
    report("证书接口超过 16 条记录返回 422",
           code == 422 and cd.get("status") == "invalid_input"
           and "16" in cd.get("error", ""),
           "code=%s error=%s" % (code, cd.get("error")))

    # --- 不可行联立 ---
    code, data = _request("POST", "/api/calibrations/solve", INFEASIBLE_PAYLOAD)
    ok = code == 409 and data.get("status") == "infeasible" \
        and data.get("rejected") is True
    report("不可行联立明确拒绝（409）", ok, "code=%s" % code)
    if ok:
        cyc = data["contradiction_cycle"]
        steps = cyc["steps"]
        structural = (
            cyc["upper_bound_sum"] < 0
            and len(steps) >= 2
            and {s["record_id"] for s in steps} <= {"R1", "R2", "R3"}
            and all(s["direction"] in ("forward", "reverse") for s in steps)
            and sum(s["upper_bound"] for s in steps) == cyc["upper_bound_sum"]
        )
        report("矛盾闭环含记录编号/使用方向/上界累加(<0)", structural,
               "上界累加=%d，步骤=%s"
               % (cyc["upper_bound_sum"],
                  [(s["record_id"], s["direction"], s["upper_bound"])
                   for s in steps]))

    # --- 非法输入 ---
    bad = {"probes": ["REF", "A", "B"], "base_probe": "REF",
           "records": [
               {"id": "R1", "a": "A", "d": "REF", "lo": 0, "hi": 1},
               {"id": "R2", "a": "B", "d": "A", "lo": 5, "hi": 1},
               {"id": "R3", "a": "B", "d": "REF", "lo": 0, "hi": 1}]}
    code, data = _request("POST", "/api/calibrations/solve", bad)
    report("非法输入（下界>上界）返回 422",
           code == 422 and data.get("status") == "invalid_input",
           "code=%s error=%s" % (code, data.get("error")))


def main():
    build_check()
    unit_tests()
    if wait_healthy():
        smoke()
    print("=" * 60, flush=True)
    if failures:
        print("验收不通过：%d 项失败 -> %s" % (len(failures), failures),
              flush=True)
        sys.exit(1)
    print("验收全部通过，退出码 0", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
