"""请求校验与规范化：热电偶编号、基准、比对记录。"""

from .solver import ValidationError


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_payload(payload, min_records=3, max_records=24):
    """校验 POST /api/calibrations/solve 与 /certificate 的请求体。

    两接口模型完全一致，仅允许的比对记录条数上限不同（求解 24 条、
    生成证书 16 条）。
    返回 (probes, records, base_id)；结构或取值非法时抛 ValidationError。
    """
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象")

    raw_probes = payload.get("probes")
    base_id = payload.get("base_probe")
    raw_records = payload.get("records")

    # ---- 探头 ----
    if not isinstance(raw_probes, list):
        raise ValidationError("probes 必须是探头编号数组")
    if not (3 <= len(raw_probes) <= 10):
        raise ValidationError("探头数量必须在 3 至 10 支之间（当前 %d 支）"
                              % len(raw_probes))
    probes = []
    seen = set()
    for item in raw_probes:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError("每支热电偶必须填唯一编号（非空字符串）")
        pid = item.strip()
        if pid in seen:
            raise ValidationError("热电偶编号重复：%s" % pid)
        seen.add(pid)
        probes.append({"id": pid})

    # ---- 基准 ----
    if not isinstance(base_id, str) or not base_id.strip():
        raise ValidationError("必须指定基准探头 base_probe")
    base_id = base_id.strip()
    if base_id not in seen:
        raise ValidationError("基准探头必须是已录入的热电偶之一")

    # ---- 比对记录 ----
    if not isinstance(raw_records, list):
        raise ValidationError("records 必须是记录数组")
    if not (min_records <= len(raw_records) <= max_records):
        raise ValidationError("比对记录数量必须在 %d 至 %d 条之间（当前 %d 条）"
                              % (min_records, max_records, len(raw_records)))

    records = []
    rids = set()
    for n, item in enumerate(raw_records, 1):
        if not isinstance(item, dict):
            raise ValidationError("第 %d 条记录必须是对象" % n)
        rid = item.get("id")
        if not isinstance(rid, str) or not rid.strip():
            raise ValidationError("第 %d 条记录缺少非空编号 id" % n)
        rid = rid.strip()
        if rid in rids:
            raise ValidationError("记录编号重复：%s" % rid)
        rids.add(rid)

        a = item.get("a")
        d = item.get("d")
        if not isinstance(a, str) or not isinstance(d, str):
            raise ValidationError("第 %d 条记录（%s）的两探头编号必须为字符串"
                                  % (n, rid))
        a, d = a.strip(), d.strip()
        if not a or not d:
            raise ValidationError("第 %d 条记录（%s）的探头编号不能为空" % (n, rid))
        if a == d:
            raise ValidationError("第 %d 条记录（%s）不能对同一支探头做比对"
                                  % (n, rid))
        if a not in seen or d not in seen:
            raise ValidationError("第 %d 条记录（%s）引用了未录入的探头"
                                  % (n, rid))
        lo = item.get("lo")
        hi = item.get("hi")
        if not (_is_int(lo) and _is_int(hi)):
            raise ValidationError("第 %d 条记录（%s）的区间端点必须是整数"
                                  % (n, rid))
        if lo > hi:
            raise ValidationError("第 %d 条记录（%s）的区间下界不能大于上界"
                                  % (n, rid))
        records.append({"id": rid, "a": a, "d": d, "lo": lo, "hi": hi})

    base_index = next(i for i, p in enumerate(probes) if p["id"] == base_id)
    return probes, records, base_index
