"""热电偶校正值联立服务（纯标准库 HTTP）。

路由：
  GET  /                      录入页面
  GET  /healthz               健康检查
  POST /api/calibrations/solve 提交探头与比对记录，返回联立结果

每次 POST 都依据当次请求体重算，不沿用上一次结论；响应携带本次输入
指纹，旧结论在记录改动后自然撤下。
"""

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .solver import solve, ValidationError as SolverValidationError
from .validation import validate_payload, ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))


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
        if path != "/api/calibrations/solve":
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
            response = solve_payload(payload)
        except (ValidationError, SolverValidationError) as exc:
            self._send_json(422, {"status": "invalid_input", "error": str(exc)})
            return
        code = 200 if response["status"] == "feasible" else 409
        self._send_json(code, response)

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
