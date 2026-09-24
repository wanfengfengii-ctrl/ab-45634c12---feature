# 热电偶校正值联立求解服务

航空发动机叶片热处理前，计量工程师把多支热电偶两两比对的**带公差闭区间**
联立为同一套校正值。服务固定基准探头校正值为 0，将全部比对记录同时纳入
一套差分约束系统求解，返回每支探头校正值的**紧确闭区间**与一组满足所有
记录的**校正见证**；记录不可同时成立时明确拒绝，并给出可定位阻断证据的
**矛盾闭环**。绝不逐条独立取中值，避免炉温窗口相互矛盾。

## 数学模型

探头 i 的校正值为 x[i]，基准探头 b 固定 x[b] = 0。

比对记录 `(A, D, [lo, hi])` 表示双向约束 `lo ≤ x[A] − x[D] ≤ hi`，拆成两条
标准差分约束（对应有向边 u→v，权 w，含义 x[v] − x[u] ≤ w）：

| 方向 | 边 | 权 | 含义 |
|---|---|---|---|
| 正向 | D → A | hi | x[A] − x[D] ≤ 区间上界 |
| 反向 | A → D | −lo | x[D] − x[A] ≤ 区间下界取反 |

- **紧确区间**：以基准为源跑 Bellman-Ford 最短路径，`dist[v]` 即 x[v] 的
  最小上界（可行域最大值）；所有边反向再跑一次取相反数，得最大下界
  （可行域最小值）。最短路径距离本身满足全部不等式，第一次结果即见证解。
- **可行性**：全 0 初始化（等价 0 权超级源）跑 n 轮，第 n 轮仍可松弛当且仅当
  存在负权闭环。
- **矛盾闭环**：取第 n 轮实际被松弛的顶点，沿前驱链回溯提取一个负环，
  映射回原始记录编号与使用方向，逐步给出上界，上界累加值必为负
  （叠加后得到 `0 ≤ 负数` 的矛盾）。
- 与基准无任何比对路径的探头校正值无界，按非法输入拒绝（422）。

## 接口

`POST /api/calibrations/solve`

```json
{
  "probes": ["REF", "TC-A", "TC-B"],
  "base_probe": "REF",
  "records": [
    {"id": "R1", "a": "TC-A", "d": "REF",  "lo": -1, "hi": 1},
    {"id": "R2", "a": "TC-B", "d": "TC-A", "lo": 0,  "hi": 2},
    {"id": "R3", "a": "TC-B", "d": "REF",  "lo": -1, "hi": 3}
  ]
}
```

约束：探头 3–10 支且编号唯一；比对记录 3–24 条，端点为整数闭区间，
两条探头不能相同且必须已录入。

- `200` 可行：`ranges`（每支探头 `{min,max,tight}` 紧确闭区间）、
  `witness`（一组满足全部记录的校正见证，基准恒为 0）、`input_fingerprint`。
- `409` 不可行：`rejected: true` 与 `contradiction_cycle`
  （`steps`：记录编号 + 使用方向 forward/reverse + 上界；
  `upper_bound_sum`：上界累加值，小于 0）。
- `422` 输入非法：中文错误说明。

响应含 `Cache-Control: no-store` 与输入指纹；**任一记录改动指纹即变**，
页面据此撤下旧结论，要求重新联立。服务本身无状态，每次 POST 全量重算。

另有 `GET /`（录入/结果页）与 `GET /healthz`（健康检查）。

## 本地运行（无需任何第三方依赖，Python 3.11 标准库）

```bash
python3 -m app.server          # 默认 0.0.0.0:8000，可用 PORT 覆盖
python3 -m unittest discover -s tests
```

## Docker / Compose

```bash
# 宿主机端口可配置（默认 8000）
WEB_PORT=8080 docker compose up --build -d web

# 一次性验收服务：代码测试 + 构建检查 + 业务 HTTP 冒烟，
# 等待 web 健康后运行，完成后自行退出并以退出码报告结果
docker compose run --build verify      # 退出码 0 通过，非 0 失败
docker compose up --build verify       # 编排方式同理
```

- `Dockerfile` 内置 `HEALTHCHECK`；`docker-compose.yml` 的 `web` 也声明
  healthcheck，`verify` 通过 `depends_on: service_healthy` 等待其就绪。
- 实现仅用 Python 标准库，镜像内无 pip 依赖。
