"""Pure C1 observation analysis. No runtime imports or physical attribution."""
from __future__ import annotations

import math
import re
from agentmon.analysis.prometheus import parse_prometheus_text


def parse_hbm_table(text):
    """Parse confirmed two-line 910B table; never parse the process section as HBM.

    Preserve the vendor's reported MB. Do not silently turn it into bytes/GiB.
    """
    rows, current, seen = [], None, set()
    for line in text.splitlines():
        if "Process id" in line:
            break
        header = re.match(r"\|\s*(\d+)\s+910B\w*\s*\|", line)
        if header:
            current = header.group(1)
            continue
        if current is None or not re.search(r"[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.\d", line):
            continue
        ratios = re.findall(r"(\d+)\s*/\s*(\d+)", line)
        if not ratios:
            continue
        used, total = map(int, ratios[-1])
        if current in seen or total <= 0 or used > total:
            raise ValueError("ambiguous/invalid NPU table")
        seen.add(current)
        rows.append({"device_id": current, "hbm_used_mb_reported": used,
                     "hbm_capacity_mb_reported": total, "unit": "npu-smi reported MB",
                     "evidence": "VERIFIED"})
        current = None
    return rows


def metric_values(text, model="deepseek-v41", engine="0"):
    values, duplicates = {}, set()
    for sample in parse_prometheus_text(text):
        if sample.labels.get("model_name") != model or sample.labels.get("engine") != engine:
            continue
        if len(sample.labels) != 2:  # no summing histogram/reason series into a scalar
            continue
        if sample.name in values:
            duplicates.add(sample.name)
        values[sample.name] = sample.value if math.isfinite(sample.value) and sample.value >= 0 else None
    for name in duplicates:
        values[name] = None
    return values


def cache_config_labels(text, engine="0"):
    samples = [s for s in parse_prometheus_text(text) if s.name == 'vllm:cache_config_info'
               and s.labels.get('engine') == engine and s.value == 1]
    return samples[0].labels if len(samples) == 1 else None


def safe_delta(before, after, *, epoch_stable=True):
    if not epoch_stable or before is None or after is None:
        return {"delta": None, "status": "NOT_OBSERVED", "reason": "missing_or_changed_epoch"}
    if not all(isinstance(x, (float, int)) and math.isfinite(x) and x >= 0 for x in (before, after)):
        return {"delta": None, "status": "NOT_OBSERVED", "reason": "invalid_counter"}
    if after < before:
        return {"delta": None, "status": "UNRESOLVED", "reason": "counter_decreased_or_reset"}
    return {"delta": after - before, "status": "VERIFIED", "scope": "OBSERVATION_WINDOW"}


def classify(row, request):
    start, end = request.get("started_ns"), request.get("ended_ns")
    if start is None or end is None:
        return "dry_run"
    if row["ended_ns"] <= start:
        return "pre"
    if row["started_ns"] >= end:
        return "post"
    if row["started_ns"] >= start and row["ended_ns"] <= end:
        return "during"
    return "overlap"


def process_epoch_stable(rows, expected_pids):
    samples = [r for r in rows if r['source'] == 'process']
    if not samples or not expected_pids:
        return False
    before = {p['pid']: p.get('starttime_ticks') for p in (samples[0].get('data') or [])}
    after = {p['pid']: p.get('starttime_ticks') for p in (samples[-1].get('data') or [])}
    return all(before.get(pid) is not None and before.get(pid) == after.get(pid) for pid in expected_pids)


def summarize(rows, request, *, epoch_stable, disk_device):
    classified = [dict(r, window=classify(r, request)) for r in rows]
    coverage = {}
    for source in ("npu", "host", "process", "disk", "metrics"):
        coverage[source] = {phase: sum(r["source"] == source and r.get("status") == "VERIFIED"
                                      and r["window"] == phase for r in classified)
                            for phase in ("pre", "during", "overlap", "post", "dry_run")}
    # Latest pre and latest post retain cleanup/statistics lag in the observation window.
    def boundaries(source):
        pre = [r for r in classified if r["source"] == source and r["window"] == "pre"]
        post = [r for r in classified if r["source"] == source and r["window"] == "post"]
        return (max(pre, key=lambda r: r["ended_ns"]) if pre else None,
                max(post, key=lambda r: r["started_ns"]) if post else None)

    metric_pre, metric_post = boundaries("metrics")
    m0 = (metric_pre.get("data") or {}) if metric_pre else {}
    m1 = (metric_post.get("data") or {}) if metric_post else {}
    deltas = {}
    for name in ("prefix_cache_queries_total", "prefix_cache_hits_total", "num_preemptions_total", "request_success_total"):
        key = "vllm:" + name
        created = key.removesuffix("_total") + "_created"
        created_stable = m0.get(created) == m1.get(created)
        deltas[name] = dict(safe_delta(m0.get(key), m1.get(key), epoch_stable=epoch_stable and created_stable),
                            scope="ENGINE_WINDOW")
        between = [(r.get('data') or {}) for r in classified if r['source'] == 'metrics'
                   and metric_pre and metric_post and r['started_ns'] >= metric_pre['started_ns']
                   and r['ended_ns'] <= metric_post['ended_ns']]
        values = [d[key] for d in between if d.get(key) is not None]
        creations = [d[created] for d in between if d.get(created) is not None]
        if any(a < b for b, a in zip(values, values[1:])) or len(set(creations)) > 1:
            deltas[name] = {'delta': None, 'status': 'UNRESOLVED', 'scope': 'ENGINE_WINDOW',
                            'reason': 'observed_counter_reset_or_created_change'}
    q, h = (deltas[n]["delta"] for n in ("prefix_cache_queries_total", "prefix_cache_hits_total"))
    ratio = h / q if q and h is not None and 0 <= h <= q else None
    deltas["prefix_ratio"] = {"value": ratio, "scope": "ENGINE_WINDOW", "status": "INFERRED" if ratio is not None else "NOT_OBSERVED"}
    deltas["reset_detection_limit"] = "finite_samples_cannot_rule_out_every_reset"
    hbm = []
    n0, n1 = boundaries("npu")
    before = {d["device_id"]: d for d in ((n0 or {}).get("data") or [])}
    after = {d["device_id"]: d for d in ((n1 or {}).get("data") or [])}
    for device in map(str, range(8)):
        during = [d["hbm_used_mb_reported"] for r in classified if r["source"] == "npu" and r["window"] == "during"
                  for d in (r.get("data") or []) if d["device_id"] == device]
        b, a = before.get(device, {}).get("hbm_used_mb_reported"), after.get(device, {}).get("hbm_used_mb_reported")
        hbm.append({"device_id": device, "before_mb_reported": b, "during_sample_peak_mb_reported": max(during) if during else None,
                    "after_mb_reported": a, "delta_mb_reported": a-b if a is not None and b is not None and epoch_stable else None})
    d0, d1 = boundaries("disk")
    disk = {key: safe_delta(((d0 or {}).get("data") or {}).get(key), ((d1 or {}).get("data") or {}).get(key), epoch_stable=epoch_stable)
            for key in ("read_bytes", "write_bytes")}
    disk.update(device=disk_device, scope="BLOCK_DEVICE_WINDOW", sector_unit_bytes=512)
    host0, host1 = boundaries("host")
    host = {"before": (host0 or {}).get("data"), "after": (host1 or {}).get("data"), "scope": "HOST"}
    host['during'] = [{"utc": r['utc'], "data": r.get('data'), "started_ns": r['started_ns'],
                       "ended_ns": r['ended_ns']} for r in classified
                      if r['source'] == 'host' and r['window'] == 'during' and r['status'] == 'VERIFIED']
    host['delta_bytes'] = {key: after - before if epoch_stable and before is not None and after is not None else None
                           for key in ('MemAvailable', 'MemFree', 'Cached', 'AnonPages', 'Shmem')
                           for before, after in [((host['before'] or {}).get(key), (host['after'] or {}).get(key))]}
    p0, p1 = boundaries("process")
    process = []
    bp = {p["pid"]: p for p in ((p0 or {}).get("data") or [])}
    ap = {p["pid"]: p for p in ((p1 or {}).get("data") or [])}
    for pid in sorted(set(bp) | set(ap)):
        b, a = bp.get(pid, {}), ap.get(pid, {})
        stable = epoch_stable and b.get("starttime_ticks") is not None and b.get("starttime_ticks") == a.get("starttime_ticks")
        process.append({"pid": pid, "role": b.get("role", a.get("role")), "before": b, "after": a,
                        "identity_stable": stable,
                        "io_deltas": {k: safe_delta(b.get(k), a.get(k), epoch_stable=stable) for k in ("read_bytes", "write_bytes")}})
    missing = [s for s, c in coverage.items() if not all(c[p] for p in ("pre", "during", "post"))]
    other_traffic = any(((r.get("data") or {}).get("vllm:num_requests_running", 0) or 0) > 0
                        or ((r.get("data") or {}).get("vllm:num_requests_waiting", 0) or 0) > 0
                        for r in classified if r["source"] == "metrics" and r["window"] == "pre")
    success_delta = deltas["request_success_total"]["delta"]
    other_traffic = other_traffic or (success_delta is not None and success_delta > 1)
    result = {"request": request, "coverage": coverage, "hbm": hbm, "host": host, "processes": process,
              "disk": disk, "metric_deltas": deltas, "epoch_stable": epoch_stable,
              "missing_full_phase_coverage": missing, "other_traffic_indicator": other_traffic,
              "attribution": "ENGINE_DEVICE_PROCESS_WINDOW_ONLY", "kv_physical_allocation": "NOT_OBSERVED",
              "offload_events": "NOT_OBSERVED", "runtime_loaded_modules": "UNRESOLVED"}
    result['cache_config_info'] = {'before': m0.get('_cache_config_info'), 'after': m1.get('_cache_config_info'),
                                    'scope': 'ENGINE_CONFIG_LABELS', 'block_semantics': 'UNRESOLVED'}
    result["kv_gauge_timeline"] = [{"utc":r["utc"],"window":r["window"],"started_ns":r["started_ns"],"ended_ns":r["ended_ns"],
                                   "kv_cache_usage_perc":(r.get('data') or {}).get('vllm:kv_cache_usage_perc'),
                                   "running":(r.get('data') or {}).get('vllm:num_requests_running'),
                                   "waiting":(r.get('data') or {}).get('vllm:num_requests_waiting')}
                                  for r in classified if r['source']=='metrics']
    def gauge_summary(key):
        during = [r['data'][key] for r in classified if r['source'] == 'metrics'
                  and r['window'] == 'during' and (r.get('data') or {}).get(key) is not None]
        return {'before': m0.get(key), 'during_sample_peak': max(during) if during else None,
                'after': m1.get(key), 'scope': 'ENGINE_GAUGE'}
    result['kv_gauges'] = {name: gauge_summary('vllm:' + name) for name in
                           ('kv_cache_usage_perc', 'num_requests_running', 'num_requests_waiting')}
    result['unobserved_process_io_pids'] = [p['pid'] for p in process if any(p['io_deltas'][k]['delta'] is None for k in ('read_bytes','write_bytes'))]
    if not request.get("post_attempts"):
        result["status"] = "DRY_RUN_OR_BLOCKED"
    elif request.get("status") != "VERIFIED":
        result["status"] = "SINGLE_REQUEST_FAILED"
    else:
        result["status"] = "PARTIAL" if missing or not epoch_stable or other_traffic or result['unobserved_process_io_pids'] else "RESOURCE_TIMELINE_VERIFIED"
    return result
