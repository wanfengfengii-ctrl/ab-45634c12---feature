"""最小可追溯证书：在全部比对记录的子集中直接搜索最小保留记录集。

要求
----
从全量记录里选出一组“保留记录”，使其**独立联立**求得的每支探头紧确
上下界与全量记录完全相同；被省掉的记录不改变任何探头相对基准的可取
校正闭区间。

选择规则（不得先任取子集再逐条贪心删除）：

1. 直接枚举所有记录子集，先最少化保留记录数；
2. 同数时，按“保留记录编号升序序列”取字典序最小的一组。

实现
----
全量模型可行（由调用方保证）时，删除约束不可能产生负环，故子集只需要
做无向连通性检查 + 两次以基准为源的 Bellman-Ford（原图取上界、反图取
下界），无需再跑负环检测。记录编号按字符串升序排序后，
itertools.combinations 天然按字典序产出，第一个命中的组合即最优解。
"""

from itertools import combinations

from .solver import solve

INF = 10**18


def _connected(n, specs, base):
    """无向并查集：保留记录必须仍把每支探头连到基准。"""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, d, _hi, _nl in specs:
        ra, rd = find(a), find(d)
        if ra != rd:
            parent[ra] = rd
    root = find(base)
    return all(find(i) == root for i in range(n))


def _subset_bounds(n, specs, base):
    """对一组保留记录独立求紧确上下界。

    specs: 每条记录 (a, d, hi, -lo)，含两条有向边
        d -> a 权 hi   （上界）
        a -> d 权 -lo  （下界取反）
    返回 (upper, lower)（lower 已对反图距离取反）；与基准不连通返回 None。
    """
    if not _connected(n, specs, base):
        return None

    # 上界：基准为源的最短距离 = 各变量最小上界
    upper = [INF] * n
    upper[base] = 0
    for _ in range(n - 1):
        changed = False
        for a, d, hi, nl in specs:
            cand = upper[d] + hi
            if cand < upper[a]:
                upper[a] = cand
                changed = True
            cand = upper[a] + nl
            if cand < upper[d]:
                upper[d] = cand
                changed = True
        if not changed:
            break

    # 下界：全部边反向再跑一次，结果取相反数
    rev = [INF] * n
    rev[base] = 0
    for _ in range(n - 1):
        changed = False
        for a, d, hi, nl in specs:
            cand = rev[a] + hi
            if cand < rev[d]:
                rev[d] = cand
                changed = True
            cand = rev[d] + nl
            if cand < rev[a]:
                rev[a] = cand
                changed = True
        if not changed:
            break

    return upper, [-v for v in rev]


def find_minimal_retention(probes, records, base_index, full_ranges):
    """直接枚举全部子集，返回最优保留记录的下标列表。

    full_ranges: 全量记录联立结果 solve().ranges。
    返回 (kept_indices, subsets_evaluated)。
    """
    n = len(probes)
    idx = {p["id"]: i for i, p in enumerate(probes)}
    specs = [
        (idx[r["a"]], idx[r["d"]], r["hi"], -r["lo"]) for r in records
    ]
    target_upper = [full_ranges[p["id"]]["max"] for p in probes]
    target_lower = [full_ranges[p["id"]]["min"] for p in probes]

    def matches(upper, lower):
        for i in range(n):
            if upper[i] != target_upper[i] or lower[i] != target_lower[i]:
                return False
        return True

    # 记录编号字符串升序；combinations 按位置字典序产出，
    # 同数第一个命中的组合即“编号升序序列字典序最小”的解。
    order = sorted(range(len(records)), key=lambda i: records[i]["id"])
    evaluated = 0
    for k in range(1, len(records) + 1):
        for combo in combinations(order, k):
            evaluated += 1
            got = _subset_bounds(n, [specs[i] for i in combo], base_index)
            if got is not None and matches(got[0], got[1]):
                return list(combo), evaluated
    # 全量集合本身必然命中（全量可行且连通），到不了这里
    raise AssertionError("不存在能复现全量紧确区间的保留记录子集")


def build_certificate(probes, records, base_index, full_result):
    """生成最小可追溯证书数据（不含 HTTP 指纹，指纹由 server 层加盖）。

    full_result 必须是全量记录 solve() 的可行结果；调用方负责先拦截
    不可行 / 不连通 / 字段非法等拒绝情形。
    """
    base_id = probes[base_index]["id"]
    kept, evaluated = find_minimal_retention(
        probes, records, base_index, full_result.ranges
    )
    kept_set = set(kept)

    # 保留记录按原始提交顺序列出，便于归档时与原表逐行对照
    retained = [records[i] for i in sorted(kept)]
    omitted = [records[i] for i in range(len(records)) if i not in kept_set]

    # 独立联立保留记录（完整 solve 流程），逐端复核与全量完全相等
    sub = solve(probes, retained, base_index)
    if not sub.feasible:
        raise AssertionError("保留记录子集独立联立不可行")
    # 保留记录的见证解必须满足全部保留记录
    for r in retained:
        diff = sub.witness[r["a"]] - sub.witness[r["d"]]
        if not (r["lo"] <= diff <= r["hi"]):
            raise AssertionError("证书见证解不满足保留记录 %s" % r["id"])

    probe_ranges = []
    for p in probes:
        pid = p["id"]
        fr = full_result.ranges[pid]
        cr = sub.ranges[pid]
        if fr["min"] != cr["min"] or fr["max"] != cr["max"]:
            raise AssertionError("证书区间与全量不一致：%s" % pid)
        probe_ranges.append({
            "probe": pid,
            "is_base": pid == base_id,
            "full": {"min": fr["min"], "max": fr["max"], "tight": fr["tight"]},
            "certificate": {"min": cr["min"], "max": cr["max"],
                           "tight": cr["tight"]},
            "min_equal": fr["min"] == cr["min"],
            "max_equal": fr["max"] == cr["max"],
        })

    return {
        "status": "certificate",
        "base_probe": base_id,
        "retained_count": len(retained),
        "omitted_count": len(omitted),
        "retained_record_ids": sorted(r["id"] for r in retained),
        "omitted_record_ids": sorted(r["id"] for r in omitted),
        "retained_records": retained,
        "omitted_records": omitted,
        "probe_ranges": probe_ranges,
        "ranges": sub.ranges,
        "witness": sub.witness,
        "selection": {
            "method": "exhaustive_subset_enumeration",
            "rule": "先最少化保留记录数；同数按保留记录编号升序序列取字典序最小",
            "subsets_evaluated": evaluated,
        },
        "note": "证书仅由保留记录独立联立求得，各探头紧确上下界与全量记录逐端相等",
    }
