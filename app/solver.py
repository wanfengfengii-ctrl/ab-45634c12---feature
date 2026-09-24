"""差分约束求解器：热电偶校正值联立。

模型
----
设探头 i 的校正值为 x[i]。基准探头 b 固定 x[b] = 0。

每条比对记录 r：探头 a、探头 d，允许温差闭区间 [lo, hi]（整数），
语义为双向约束：

    lo <= x[a] - x[d] <= hi

拆成两条标准差分约束（x[v] - x[u] <= w 对应有向边 u -> v，权 w）：

    x[a] - x[d] <= hi        边 d -> a，权 hi   （记录“正向”使用，上界 hi）
    x[d] - x[a] <= -lo       边 a -> d，权 -lo  （记录“反向”使用，上界 -lo）

紧确可行区间（图连通、基准固定为 0）
------------------------------------
以基准为源跑最短路径，dist[v] 是限制 x[v] 的最短约束链，即可行域中
x[v] 的最大值（最小上界）；把每条边反向后再跑一次，得到 -x[v] 的最小
上界，其相反数即 x[v] 的最小值（最大下界）。最短路径距离本身满足全部
不等式，故第一次的 dist 就是一组满足所有记录的校正见证。

可行性 / 矛盾闭环
-----------------
以“所有顶点距离初始 0”（等价接入 0 权超级源）跑 Bellman-Ford：第 n 轮
后仍可松弛当且仅当存在负权闭环。负环对应一组叠加后 0 <= 负数的矛盾，
沿前驱指针可取出该闭环，并把每条有向边映射回原始记录编号与使用方向，
各边上界（权）累加为负数，供工程师定位阻断证据。
"""

from dataclasses import dataclass, field

INF = 10**18


@dataclass(frozen=True)
class Edge:
    """一条标准差分约束：x[to] - x[frm] <= weight。"""

    frm: int
    to: int
    weight: int
    record_index: int  # 原始比对记录的 0 基下标（-1 表示非记录边）
    # "forward": x[a]-x[d] <= hi ；"reverse": x[d]-x[a] <= -lo
    direction: str


@dataclass
class Cycle:
    """矛盾闭环：边按有向环游顺序排列，权和为负。"""

    edges: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(e.weight for e in self.edges)

    def describe(self, records):
        """渲染为定位说明：逐步给出原始记录编号、使用方向、上界累加值。"""
        steps = []
        for e in self.edges:
            r = records[e.record_index]
            if e.direction == "forward":
                # 边 d -> a，权 hi
                usage = "正向"
                text = "记录 %s 正向：x[%s] - x[%s] ≤ %d（区间上界）" % (
                    r["id"], r["a"], r["d"], e.weight
                )
            else:
                # 边 a -> d，权 -lo
                usage = "反向"
                text = "记录 %s 反向：x[%s] - x[%s] ≤ %d（区间下界 %d 取反）" % (
                    r["id"], r["d"], r["a"], e.weight, r["lo"]
                )
            steps.append(
                {
                    "record_id": r["id"],
                    "direction": e.direction,
                    "usage": usage,
                    "upper_bound": int(e.weight),
                    "text": text,
                }
            )
        return {
            "steps": steps,
            "upper_bound_sum": int(self.total),
            "explanation": (
                "沿闭环把 %d 条有向不等式同向叠加，变量两两抵消，"
                "得到 0 ≤ %d，不成立；因此全部比对记录无法同时满足。"
                "上界累加值 = %s = %d。"
            )
            % (
                len(self.edges),
                self.total,
                " + ".join(str(e.weight) for e in self.edges),
                self.total,
            ),
        }


@dataclass
class SolveResult:
    feasible: bool
    ranges: dict = field(default_factory=dict)    # 探头编号 -> {min,max,tight}
    witness: dict = field(default_factory=dict)   # 探头编号 -> 见证校正值
    cycle: object = None                          # Cycle


class ValidationError(ValueError):
    """输入数据不满足联立前提（如探头与基准不连通）。"""


def _relax_scan(edges, dist, pred=None):
    """扫描一轮全部边做松弛，返回是否发生松弛。"""
    changed = False
    for e in edges:
        if dist[e.frm] >= INF:
            continue
        nd = dist[e.frm] + e.weight
        if nd < dist[e.to]:
            dist[e.to] = nd
            if pred is not None:
                pred[e.to] = e
            changed = True
    return changed


def _build_edges(probes, records):
    idx = {p["id"]: i for i, p in enumerate(probes)}
    edges = []
    for ri, r in enumerate(records):
        u = idx[r["a"]]
        v = idx[r["d"]]
        edges.append(Edge(v, u, r["hi"], ri, "forward"))  # x[a]-x[d] <= hi
        edges.append(Edge(u, v, -r["lo"], ri, "reverse"))  # x[d]-x[a] <= -lo
    return edges


def _check_connected(n, edges, base):
    """无向连通性：每支探头都必须能经比对记录关联到基准，否则校正值无界。"""
    adj = [[] for _ in range(n)]
    for e in edges:
        adj[e.frm].append(e.to)
        adj[e.to].append(e.frm)
    seen = {base}
    stack = [base]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def _extract_negative_cycle(n, edges):
    """全 0 初始化跑 Bellman-Ford，定位一个负权闭环。

    若第 n 轮松弛仍有顶点被更新，则该点的前驱链必进入某个负环：取第 n 轮
    被松弛的顶点 y，沿前驱走 n 步保证落到环上，再继续沿前驱走回该点即取
    出完整闭环（关键：第 n 轮也要真正执行松弛以更新前驱）。
    """
    dist = [0] * n
    pred = [None] * n
    y = None
    for round_i in range(n):
        changed = False
        for e in edges:
            nd = dist[e.frm] + e.weight
            if nd < dist[e.to]:
                dist[e.to] = nd
                pred[e.to] = e
                changed = True
                if round_i == n - 1:
                    y = e.to
        if not changed:
            return None  # 提前收敛，无负环
    if y is None:
        return None

    # 沿前驱走 n 步，必落入负环上的顶点
    for _ in range(n):
        pe = pred[y]
        if pe is None:
            return None
        y = pe.frm
    start = y

    collected = []
    cur = start
    for _ in range(n + 1):
        pe = pred[cur]
        if pe is None:
            return None
        collected.append(pe)
        cur = pe.frm
        if cur == start:
            break
    else:
        return None

    # 收集顺序与有向环游方向相反，反转后校验首尾相接且权和为负
    collected.reverse()
    for i, e in enumerate(collected):
        nxt = collected[(i + 1) % len(collected)]
        if e.to != nxt.frm:
            return None
    if sum(e.weight for e in collected) >= 0:
        return None
    return Cycle(edges=collected)


def _shortest_from_source(n, edges, source):
    dist = [INF] * n
    dist[source] = 0
    for _ in range(n - 1):
        if not _relax_scan(edges, dist):
            break
    return dist


def solve(probes, records, base_index):
    """求解校正值联立问题。

    probes: [{"id": 唯一编号}, ...]（下标即顶点号）
    records: [{"id": 记录编号, "a": 探头编号, "d": 探头编号, "lo": int, "hi": int}]
    base_index: 基准探头下标，校正值固定为 0
    返回 SolveResult；探头与基准不连通时抛 ValidationError。
    """
    n = len(probes)
    edges = _build_edges(probes, records)

    reachable = _check_connected(n, edges, base_index)
    if len(reachable) != n:
        missing = [probes[i]["id"] for i in range(n) if i not in reachable]
        raise ValidationError(
            "探头 %s 与基准探头 %s 之间无比对路径，校正值无界，无法联立"
            % ("、".join(missing), probes[base_index]["id"])
        )

    cycle = _extract_negative_cycle(n, edges)
    if cycle is not None:
        return SolveResult(feasible=False, cycle=cycle)

    # 可行：以基准为源求每个变量的最小上界（= 可行域最大值）
    dist_max = _shortest_from_source(n, edges, base_index)
    # 边全部反向再求一次，得到下界
    rev_edges = [Edge(e.to, e.frm, e.weight, e.record_index, e.direction)
                 for e in edges]
    dist_neg = _shortest_from_source(n, rev_edges, base_index)

    ranges = {}
    witness = {}
    base_id = probes[base_index]["id"]
    for i, p in enumerate(probes):
        upper = int(dist_max[i])
        lower = -int(dist_neg[i])
        assert lower <= upper, (p["id"], lower, upper)
        ranges[p["id"]] = {"min": lower, "max": upper, "tight": lower == upper}
        witness[p["id"]] = upper  # 最短路径距离本身满足全部约束
    witness[base_id] = 0
    ranges[base_id] = {"min": 0, "max": 0, "tight": True}

    return SolveResult(feasible=True, ranges=ranges, witness=witness)
