"""热电偶校正值联立服务（纯标准库 HTTP）。

路由：
  GET  /                            录入页面
  GET  /healthz                     健康检查
  POST /api/calibrations/solve      提交探头与比对记录，返回联立结果
  POST /api/calibrations/certificate  在可行模型上生成最小可追溯证书

每次 POST 都依据当次请求体重算，不沿用上一次结论；响应携带本次输入
指纹，旧结论在记录改动后自然撤下。
"""

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .solver import (
    certificate,
    solve,
    ValidationError as SolverValidationError,
)
from .validation import validate_payload, ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
SOLVE_PATH = "/api/calibrations/solve"
CERTIFICATE_PATH = "/api/calibrations/certificate"
CERTIFICATE_MAX_RECORDS = 16


def _fingerprint(probes, records, base_id):
    blob = json.dumps(
        {"probes": probes, "base": base_id, "records": records},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def solve_payload(payload):
    """纯逻辑入口，便于测试：返回可直接 JSON 化的响应字典。"""
    probes, records, base_index = validate_payload(payload)
    base_id = probes[base_index]["id"]
    result = solve(probes, records, base_index)

    response = {
        "status": "feasible" if result.feasible else "infeasible",
        "base_probe": base_id,
        "input_fingerprint": _fingerprint(probes, records, base_id),
    }
    if result.feasible:
        # 再独立核验一遍见证解，杜绝脏结果
        for r in records:
            diff = result.witness[r["a"]] - result.witness[r["d"]]
            if not (r["lo"] <= diff <= r["hi"]):
                raise AssertionError("见证解不满足记录 %s" % r["id"])
        response["ranges"] = result.ranges
        response["witness"] = result.witness
        response["note"] = "区间为全部比对记录联立后的紧确可行闭区间"
    else:
        response["rejected"] = True
        response["contradiction_cycle"] = result.cycle.describe(records)
    return response


def certificate_payload(payload):
    """最小可追溯证书逻辑入口。

    仅接受 3–10 支探头、3–16 条记录；字段非法（422）、与基准不连通（422）
    或全量模型不可行（409，带矛盾闭环）时沿用求解接口的明确拒绝语义，
    不产生证书。成功时直接在全部记录子集中找到保留记录最少、同数字典序
    最小的一组，其独立求得的每支探头紧确上下界与全量逐端相等。
    """
    probes, records, base_index = validate_payload(
        payload, max_records=CERTIFICATE_MAX_RECORDS)
    base_id = probes[base_index]["id"]

    # 先跑全量求解：不可行时给出与 solve 完全一致的拒绝体
    solved = solve(probes, records, base_index)
    if not solved.feasible:
        return {
            "status": "infeasible",
            "base_probe": base_id,
            "input_fingerprint": _fingerprint(probes, records, base_id),
            "rejected": True,
            "contradiction_cycle": solved.cycle.describe(records),
        }

    cert = certificate(probes, records, base_index)
    if not cert.feasible:  # 理论不可达：solve 已确认可行且连通
        raise SolverValidationError("证书搜索失败：模型不可行或与基准不连通")

    kept_set = frozenset(cert.kept_indices)
    retained = [records[i] for i in cert.kept_indices]
    omitted = [r for i, r in enumerate(records) if i not in kept_set]

    ranges = {}
    for i, p in enumerate(probes):
        f_lo, f_hi = cert.full_bounds[i]
        c_lo, c_hi = cert.cert_bounds[i]
        # 逐端相等是证书成立的硬条件，再独立断言一次
        assert (f_lo, f_hi) == (c_lo, c_hi), (p["id"],)
        ranges[p["id"]] = {
            "full": {"min": f_lo, "max": f_hi, "tight": f_lo == f_hi},
            "certificate": {"min": c_lo, "max": c_hi, "tight": c_lo == c_hi},
            "min_equal": f_lo == c_lo,
            "max_equal": f_hi == c_hi,
        }

    return {
        "status": "certified",
        "base_probe": base_id,
        "input_fingerprint": _fingerprint(probes, records, base_id),
        "certificate_fingerprint": _fingerprint(probes, retained, base_id),
        "retained_count": len(retained),
        "omitted_count": len(omitted),
        "retained_records": retained,
        "omitted_records": omitted,
        "ranges": ranges,
        "note": (
            "仅用 %d 条保留记录独立联立，每支探头的紧确上下界与 %d 条"
            "全量记录逐端相等；省略记录不改变任一探头相对基准的可取闭区间。"
        ) % (len(retained), len(records)),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ThermocoupleCalib/1.0"

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    body = f.read()
            except OSError:
                self._send_json(500, {"error": "页面缺失"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in (SOLVE_PATH, CERTIFICATE_PATH):
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            self._send_json(400, {"error": "请求体为空或过大"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return
        try:
            if path == CERTIFICATE_PATH:
                response = certificate_payload(payload)
            else:
                response = solve_payload(payload)
        except (ValidationError, SolverValidationError) as exc:
            self._send_json(422, {"status": "invalid_input", "error": str(exc)})
            return
        # solve: feasible -> 200 / infeasible -> 409
        # certificate: certified -> 200 / infeasible -> 409
        if response["status"] == "infeasible":
            self._send_json(409, response)
        else:
            self._send_json(200, response)

    def log_message(self, fmt, *args):
        # 与 Docker 日志统一走 stderr，保持简洁
        import sys
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def create_server(host="0.0.0.0", port=8000):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    httpd = create_server(host, port)
    print("热电偶校正联立服务监听 %s:%d" % (host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
