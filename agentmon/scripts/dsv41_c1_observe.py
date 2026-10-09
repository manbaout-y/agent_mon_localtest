#!/usr/bin/env python3
"""Default zero-POST dry run; explicit single-request mode requires a validated dry run.

Uses existing Host readers and Prometheus parser. No hook/model import/restart.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agentmon.analysis.c1_observations import cache_config_labels, metric_values, parse_hbm_table, process_epoch_stable, summarize
from agentmon.analysis.c1_reports import write_reports
from agentmon.sampling.host import read_key_value_kb, read_diskstats

EXPECTED = {"vllm": "0.27.1+empty", "vllm-ascend": "0.1.dev5097+ge43cf1e9f",
            "vllm_git": "6e448d0ea9bf3d88d898b65449ca6dc2aec170ac",
            "ascend_git": "e43cf1e9f5d9bead076853aa6bcacb671465de94"}
IMAGE = "sha256:cb3e81f10188237ab42e217017c5b2a4348b432edd60c8da9aedb97e346955f4"
MODEL_PATH = "/data/node0_disk2/chenyang/models/DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound"
VERSION_SCRIPT = '''import importlib.metadata as md,json,subprocess
from pathlib import Path
d={}
for n in ('vllm','vllm-ascend'):
 try:d[n]=md.version(n)
 except md.PackageNotFoundError:d[n]=None
for k,p in (('vllm_git','/vllm-workspace/vllm'),('ascend_git','/vllm-workspace/vllm-ascend')):
 r=subprocess.run(['git','-C',p,'rev-parse','HEAD'],capture_output=True,text=True,timeout=5)
 d[k]=r.stdout.strip() if r.returncode==0 else None
args=Path('/proc/1/cmdline').read_bytes().decode().split(chr(0))
d['argv_fields']={}
d['expert_parallel_enabled']='--enable-expert-parallel' in args
for key in ('--max-num-seqs','--max-model-len','--block-size','--port','--served-model-name','--tensor-parallel-size'):
 for i,a in enumerate(args):
  if a==key and i+1<len(args):d['argv_fields'][key]=args[i+1]
  elif a.startswith(key+'='):d['argv_fields'][key]=a.split('=',1)[1]
print(json.dumps(d))
'''


def utc():
    return datetime.now(timezone.utc).isoformat()


def json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def read_observed(path):
    try:
        return Path(path).read_text(encoding="utf-8"), {"status": "VERIFIED"}
    except OSError as exc:
        return None, {"status": "NOT_OBSERVED", "reason": type(exc).__name__, "errno": exc.errno, "path": str(path)}


def process_sample(pid, role, *, pss=False, proc_root=Path('/proc')):
    base = proc_root / str(pid)
    status, state = read_observed(base / "status")
    stat, stat_state = read_observed(base / "stat")
    data = {"pid": pid, "role": role, "rss_bytes": None, "pss_bytes": None,
            "read_bytes": None, "write_bytes": None, "starttime_ticks": None,
            "sources": {"status": state, "stat": stat_state}}
    if stat:
        try:
            data["starttime_ticks"] = int(stat.rsplit(')', 1)[1].split()[19])
        except (IndexError, ValueError):
            data["sources"]["stat"] = {"status": "NOT_OBSERVED", "reason": "invalid_proc_stat"}
    if status:
        for line in status.splitlines():
            key, _, value = line.partition(':')
            if key in ("VmRSS", "RssAnon", "RssFile", "RssShmem", "VmSwap"):
                try:
                    data["rss_bytes" if key == "VmRSS" else key + "_bytes"] = int(value.split()[0])*1024
                except (ValueError, IndexError):
                    pass
    io, io_state = read_observed(base / "io")
    data["sources"]["io"] = io_state
    if io:
        for line in io.splitlines():
            key, _, value = line.partition(':')
            if key in ("read_bytes", "write_bytes"):
                try:
                    data[key] = int(value)
                except ValueError:
                    pass
    if pss:
        smaps, pss_state = read_observed(base / "smaps_rollup")
        data["sources"]["pss"] = pss_state
        if smaps:
            for line in smaps.splitlines():
                if line.startswith('Pss:'):
                    try:
                        data["pss_bytes"] = int(line.split()[1])*1024
                    except (ValueError, IndexError):
                        pass
    return data


def single_request(output, *, timeout=45, connection_factory=http.client.HTTPConnection):
    """Exactly one attempt, no redirects/retries, absolute deadline plus bounded reads."""
    rid = output.name
    evidence = {"agent_run_id": rid, "model_request_id": str(uuid.uuid4()), "request_attempt_id": str(uuid.uuid4()),
                "post_attempts": 0, "status": "NOT_OBSERVED", "api_request_id": None,
                "prompt_tokens": None, "completion_tokens": None, "server_terminal": "UNRESOLVED"}
    prompt = "逐行列出1到20，每行写序号和观测测试四个字，不要解释。"
    payload = {"model": "deepseek-v41", "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 128, "stream": True, "stream_options": {"include_usage": True},
               "chat_template_kwargs": {"thinking": False}}
    evidence["request_parameters"] = {k: v for k,v in payload.items() if k != "messages"}
    evidence["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    evidence.update(started_ns=time.monotonic_ns(), started_utc=utc(), post_attempts=1)
    # Durable marker before any network attempt. A failed response still consumes this run's attempt.
    json_file(output / "request_attempt_started.json", evidence)
    connection = None
    first = None
    content_size, chunks, done, finish = 0, 0, False, None
    deadline = time.monotonic() + timeout
    digest = hashlib.sha256()
    try:
        connection = connection_factory("127.0.0.1", 8800, timeout=timeout)
        connection.request("POST", "/v1/chat/completions", json.dumps(payload).encode(),
                           {"Content-Type": "application/json", "X-AgentMon-Run-Id": rid,
                            "X-AgentMon-Model-Request-Id": evidence["model_request_id"]})
        response = connection.getresponse()
        evidence["http_status"] = response.status
        if response.status != 200:
            raise ValueError("HTTP_STATUS_" + str(response.status))
        if "text/event-stream" not in (response.getheader("Content-Type") or ""):
            raise ValueError("NOT_SSE_RESPONSE")
        total = 0
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("absolute_deadline")
            read_socket = getattr(connection, "sock", None) or getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if read_socket:
                read_socket.settimeout(remaining)
            line = response.readline(65537)
            total += len(line)
            if len(line) > 65536 or total > 1024*1024:
                raise ValueError("SSE_LIMIT_EXCEEDED")
            if not line:
                break
            if not line.startswith(b"data:"):
                continue
            part = line[5:].strip()
            if part == b"[DONE]":
                done = True
                break
            item = json.loads(part)
            if item.get("error"):
                raise ValueError("SSE_ERROR")
            api_id = item.get("id")
            if api_id:
                if evidence["api_request_id"] not in (None, api_id):
                    raise ValueError("API_ID_CHANGED")
                evidence["api_request_id"] = api_id
            usage = item.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens"):
                if isinstance(usage.get(key), int) and usage[key] >= 0:
                    evidence[key] = usage[key]
            for choice in item.get("choices", []):
                content = (choice.get("delta") or {}).get("content")
                if content:
                    if first is None:
                        first = time.monotonic_ns()
                    chunks += 1
                    content_size += len(content)
                    digest.update(content.encode())
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        evidence["status"] = "VERIFIED" if done and finish in ("stop", "length") and content_size and evidence["api_request_id"] else "NOT_OBSERVED"
        evidence["server_terminal"] = "VERIFIED" if done and finish else "UNRESOLVED"
    except Exception as exc:
        evidence.update(status="NOT_OBSERVED", error_type=type(exc).__name__)
        # Messages from our own validation only; never log arbitrary response bodies or credentials.
        if isinstance(exc, ValueError):
            evidence["validation_error"] = str(exc)[:100]
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError as exc:
                evidence['connection_close_error'] = type(exc).__name__
        evidence.update(ended_ns=time.monotonic_ns(), ended_utc=utc(), finish_reason=finish,
                        content_characters=content_size, content_events=chunks, content_sha256=digest.hexdigest())
        evidence["e2e_seconds"] = (evidence["ended_ns"]-evidence["started_ns"])/1e9
        evidence["content_ttft_seconds"] = (first-evidence["started_ns"])/1e9 if first is not None else None
        json_file(output / "response_summary.json", evidence)
        (output / "request_evidence.jsonl").write_text(json.dumps(evidence, ensure_ascii=False)+"\n", encoding="utf-8")
    return evidence


class Collector:
    def __init__(self, args):
        self.args, self.output = args, args.run_dir
        self.rows, self.commands, self.processes = [], [], []
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.phase, self.threads = "pre", []
        (self.output / "raw").mkdir()
        (self.output / "samples").mkdir()

    def command(self, argv, *, input_text=None):
        started, wall = time.monotonic_ns(), utc()
        try:
            r = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=10)
            code, out, err = r.returncode, r.stdout, r.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, out, err = None, "", type(exc).__name__
        record = {"argv": argv, "started_ns": started, "ended_ns": time.monotonic_ns(), "utc": wall, "returncode": code,
                  "stdout_truncated": len(out) > 2*1024*1024, "stderr_truncated": len(err) > 4000}
        with self.lock:
            index = len(self.commands)
            record["raw_ref"] = f"raw/command_{index:04}.txt"
            (self.output / record["raw_ref"]).write_text(out[:2*1024*1024]+"\nSTDERR:\n"+err[:4000], encoding="utf-8")
            self.commands.append(record)
        if code != 0:
            raise RuntimeError(f"command_failed_{code}; see {record['raw_ref']}")
        return out

    def docker(self, *argv, **kwargs):
        return self.command(["docker", "-H", self.args.socket, *argv], **kwargs)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", 8800, timeout=3)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read(16*1024*1024+1)
            if response.status != 200 or len(raw) > 16*1024*1024:
                raise ValueError("HTTP_GET_FAILED_OR_OVERSIZE")
            return raw.decode('utf-8')
        finally:
            conn.close()

    def identity(self):
        fmt = '{"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State}},"network":{{json .HostConfig.NetworkMode}}}'
        return json.loads(self.docker("inspect", self.args.container, "--format", fmt))

    def record(self, source, collect):
        start, wall, phase = time.monotonic_ns(), utc(), self.phase
        try:
            data = collect()
            status, error = "VERIFIED", None
            if data in (None, {}, []):
                status, error = "NOT_OBSERVED", "empty_or_unparsed"
            if source == 'process' and data and any(p.get('rss_bytes') is None or p.get('starttime_ticks') is None for p in data):
                status, error = 'NOT_OBSERVED', 'process_memory_or_identity_missing'
        except Exception as exc:
            data, status, error = None, "NOT_OBSERVED", str(exc)[:150]
        row = {"source": source, "phase_started": phase, "phase_ended": self.phase,
               "started_ns": start, "ended_ns": time.monotonic_ns(), "utc": wall,
               "clock_domain": "HOST_MONOTONIC", "status": status, "error": error, "data": data}
        row['scope'], row['data_source'], row['units'] = {
            'npu': ('DEVICE', 'npu-smi info', 'vendor reported MB'),
            'host': ('HOST', '/proc/meminfo', 'bytes; Linux kB x 1024'),
            'process': ('PROCESS', '/proc/host_pid/{stat,status,io,smaps_rollup}', 'bytes; starttime in ticks'),
            'disk': ('BLOCK_DEVICE', '/proc/diskstats:' + self.args.disk_device, 'bytes; sector=512 bytes; times=ms'),
            'metrics': ('ENGINE', 'http://127.0.0.1:8800/metrics', 'metric-native; KV gauge ratio, prefix counters tokens'),
        }[source]
        row["duration_seconds"] = (row["ended_ns"]-start)/1e9
        with self.lock:
            self.rows.append(row)
            with (self.output / "samples" / (source+".jsonl")).open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=False)+"\n")
        return row

    def npu(self):
        rows = parse_hbm_table(self.command(["npu-smi", "info"]))
        if {r["device_id"] for r in rows} != set(map(str, range(8))):
            raise ValueError("EXPECTED_EIGHT_HBM_ROWS")
        return rows

    def metrics(self):
        text = self.get("/metrics")
        with self.lock:
            index = sum(r["source"] == "metrics" for r in self.rows)
        (self.output / "raw" / f"metrics_{index:04}.prom").write_text(text, encoding='utf-8')
        values = metric_values(text)
        values['_cache_config_info'] = cache_config_labels(text)
        required = ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc",
                    "prefix_cache_queries_total", "prefix_cache_hits_total")
        if any(values.get("vllm:"+key) is None for key in required):
            raise ValueError("METRIC_SCOPE_OR_FIELDS_MISSING")
        return values

    def resources(self, *, pss=False):
        self.record("host", lambda: {k:v for k,v in read_key_value_kb('/proc/meminfo').items()
                                   if k in ('MemTotal','MemAvailable','MemFree','Cached','Buffers','AnonPages','Shmem')})
        self.record("process", lambda: [process_sample(p['pid'], p['role'], pss=pss) for p in self.processes])
        self.record("disk", lambda: read_diskstats().get(self.args.disk_device))

    def preflight(self):
        identity = self.identity()
        if identity["state"]["Status"] != "running" or identity["image"] != IMAGE:
            raise ValueError("CONTAINER_OR_IMAGE_MISMATCH")
        meta = json.loads(self.docker("exec", "-i", "-e", "AGENTMON_VLLM_P2_ENABLE=0", self.args.container,
                                     self.args.container_python, "-I", "-", input_text=VERSION_SCRIPT))
        identity["source_metadata"] = meta
        if any(meta.get(key) != value for key,value in EXPECTED.items()):
            raise ValueError("VERSION_OR_SOURCE_COMMIT_MISMATCH")
        fields = meta['argv_fields']
        if fields.get('--max-num-seqs') != '1' or fields.get('--port') != '8800' or fields.get('--served-model-name') != 'deepseek-v41' or fields.get('--tensor-parallel-size') != '8' or not meta.get('expert_parallel_enabled'):
            raise ValueError("SERVING_PROFILE_MISMATCH")
        top = self.docker("top", self.args.container, "-eo", "pid,ppid,comm")
        for line in top.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            role = 'worker' if 'Worker' in parts[2] else 'engine' if 'EngineCor' in parts[2] else 'api' if int(parts[0]) == identity['state']['Pid'] else None
            if role:
                self.processes.append({"pid": int(parts[0]), "role": role})
        if sum(p['role'] == 'worker' for p in self.processes) != 8:
            raise ValueError("WORKER_IDENTITY_INCOMPLETE")
        models = json.loads(self.get('/v1/models'))
        if not any(x.get('id') == 'deepseek-v41' and x.get('max_model_len') == 262144 for x in models.get('data', [])):
            raise ValueError("MODEL_ENDPOINT_MISMATCH")
        mount = json.loads(self.command(['findmnt','-J','-T',self.args.model_path,'-o','SOURCE,TARGET,FSTYPE']))
        source = mount['filesystems'][0]['source']
        if Path(os.path.realpath(source)).name != self.args.disk_device:
            raise ValueError("MODEL_BLOCK_DEVICE_MISMATCH")
        identity.update(model_mount=mount, processes=self.processes, hostname=socket.gethostname(),
                        runtime_loaded_modules="UNRESOLVED", endpoint="http://127.0.0.1:8800/v1")
        identity.update(observer_uid=os.geteuid(), observer_gid=os.getegid(),
                        model_card={k: x.get(k) for x in models['data'] if x.get('id') == 'deepseek-v41'
                                    for k in ('id', 'max_model_len')})
        if identity['network'] != 'host':
            ports = self.docker('port',self.args.container,'8800/tcp')
            if not any(line.endswith(':8800') for line in ports.splitlines()):
                raise ValueError("HOST_ENDPOINT_PORT_MAPPING_UNRESOLVED")
        identity['workspace_commit'] = self.command(['git','rev-parse','HEAD']).strip()
        json_file(self.output/'runtime_identity.json', identity)
        return identity

    def start(self):
        for source, interval, collect in (('npu',self.args.npu_interval,self.npu),
                                          ('metrics',self.args.interval,self.metrics),
                                          ('resources',self.args.interval,self.resources)):
            def loop(source=source, interval=interval, collect=collect):
                while not self.stop.is_set():
                    started = time.monotonic()
                    if source == 'resources':
                        collect()
                    else:
                        self.record(source, collect)
                    self.stop.wait(max(0.1,interval-(time.monotonic()-started)))
            thread = threading.Thread(target=loop, name='c1-'+source, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
        return not any(t.is_alive() for t in self.threads)


def ready(rows):
    for source in ('npu','host','process','disk','metrics'):
        good = [r for r in rows if r['source'] == source and r['status'] == 'VERIFIED']
        if not good:
            return False
    processes = [r for r in rows if r['source'] == 'process'][-1]['data']
    if not processes or any(p.get('rss_bytes') is None or p.get('starttime_ticks') is None for p in processes):
        return False
    metrics = [r for r in rows if r['source'] == 'metrics']
    return bool(metrics) and all(r['status'] == 'VERIFIED' and not r['data'].get('vllm:num_requests_running')
                                 and not r['data'].get('vllm:num_requests_waiting') for r in metrics)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='new directory only')
    parser.add_argument('--mode', choices=('dry-run','single-request'), default='dry-run')
    parser.add_argument('--validated-dry-run', type=Path)
    parser.add_argument('--confirm-exclusive-window', action='store_true')
    parser.add_argument('--socket', default='unix:///run/docker-v41.sock')
    parser.add_argument('--container', default='dsv41-smoke-256k')
    parser.add_argument('--container-python', default='/usr/local/python3.12.13/bin/python3')
    parser.add_argument('--model-path', default=MODEL_PATH)
    parser.add_argument('--disk-device', default='nvme1n1p1')
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--npu-interval', type=float, default=2)
    parser.add_argument('--pre-seconds', type=float, default=10)
    parser.add_argument('--post-seconds', type=float, default=10)
    parser.add_argument('--request-timeout', type=float, default=45)
    args = parser.parse_args(argv)
    for key in ('interval','npu_interval','pre_seconds','post_seconds','request_timeout'):
        value = getattr(args,key)
        if not math.isfinite(value) or value < 0.5 or value > 60:
            parser.error('durations must be finite and within 0.5..60 seconds')
    if args.mode == 'single-request' and (not args.confirm_exclusive_window or not args.validated_dry_run):
        parser.error('single-request requires --confirm-exclusive-window and --validated-dry-run')
    args.run_dir.mkdir(parents=True, exist_ok=False)
    collector = Collector(args)
    identity, request = {}, {'post_attempts':0,'status':'NOT_OBSERVED'}
    preflight, epoch = {'status':'NOT_OBSERVED'}, False
    try:
        identity = collector.preflight()
        if args.mode == 'single-request':
            prior = json.loads((args.validated_dry_run/'preflight.json').read_text(encoding='utf-8'))
            old = json.loads((args.validated_dry_run/'runtime_identity.json').read_text(encoding='utf-8'))
            if prior.get('status') != 'DRY_RUN_VERIFIED' or any(old.get(k) != identity.get(k) for k in ('id','image','hostname','workspace_commit','processes','model_mount')) or old['state']['StartedAt'] != identity['state']['StartedAt']:
                raise ValueError('DRY_RUN_EVIDENCE_MISMATCH')
        collector.resources(pss=True)
        collector.start()
        time.sleep(args.pre_seconds)
        if not ready(collector.rows):
            raise ValueError('SAMPLING_UNAVAILABLE_OR_PRE_WINDOW_NOT_IDLE')
        preflight = {'status':'DRY_RUN_VERIFIED','parameters':vars(args)|{'run_dir':str(args.run_dir),'validated_dry_run':str(args.validated_dry_run)},
                     'exclusive_window_operator_confirmed':args.confirm_exclusive_window,
                     'runtime_loaded_modules':'UNRESOLVED','proc_io_may_be_denied':True}
        json_file(args.run_dir/'preflight.json', preflight)
        if args.mode == 'single-request':
            collector.phase = 'during'
            request = single_request(args.run_dir, timeout=args.request_timeout)
            collector.phase = 'post'
            time.sleep(args.post_seconds)
            collector.resources(pss=True)
        after = collector.identity()
        epoch = all(after.get(k) == identity.get(k) for k in ('id','image')) and after['state']['StartedAt'] == identity['state']['StartedAt'] and after['state']['Pid'] == identity['state']['Pid']
    except Exception as exc:
        preflight.update(status='BLOCKED', error_type=type(exc).__name__, reason=str(exc)[:180])
    finally:
        # An output failure after POST must not make its consumed attempt disappear.
        marker = args.run_dir/'request_attempt_started.json'
        if not request.get('post_attempts') and marker.exists():
            request = json.loads(marker.read_text(encoding='utf-8'))
            request.update(status='NOT_OBSERVED', server_terminal='UNRESOLVED',
                           ended_ns=time.monotonic_ns(), ended_utc=utc(),
                           reason='attempt_started_but_final_response_evidence_unavailable')
        joined = collector.close()
        preflight['samplers_joined'] = joined
        if not joined:
            preflight['status'] = 'BLOCKED'
        expected_pids = [p['pid'] for p in getattr(collector, 'processes', [])]
        if expected_pids:
            epoch = epoch and process_epoch_stable(collector.rows, expected_pids)
        if preflight.get('status') == 'DRY_RUN_VERIFIED' and not epoch:
            preflight.update(status='BLOCKED', reason='CONTAINER_OR_PROCESS_EPOCH_CHANGED_OR_MISSING')
        json_file(args.run_dir/'preflight.json', preflight)
        result = summarize(collector.rows, request, epoch_stable=epoch, disk_device=args.disk_device)
        if request.get('status') == 'VERIFIED' and preflight['status'] == 'BLOCKED':
            result['status'] = 'PARTIAL'
        result['preflight'] = preflight
        result['requested_intervals_seconds'] = {'npu':args.npu_interval,'host_process_disk_metrics':args.interval}
        result['hook_installed'] = False
        result['service_modified'] = False
        json_file(args.run_dir/'commands.json',collector.commands)
        (args.run_dir/'commands.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in collector.commands),encoding='utf-8')
        write_reports(args.run_dir,result,identity,collector.rows,collector.commands)
    print('RUN_DIR='+str(args.run_dir.resolve()))
    print('PREFLIGHT='+preflight['status']+'; OBSERVATION='+result['status']+'; POST_ATTEMPTS='+str(request.get('post_attempts',0)))
    if args.mode == 'dry-run':
        return 0 if preflight['status'] == 'DRY_RUN_VERIFIED' else 2
    return 0 if request.get('status') == 'VERIFIED' and epoch and joined else 2


if __name__ == '__main__':
    raise SystemExit(main())
