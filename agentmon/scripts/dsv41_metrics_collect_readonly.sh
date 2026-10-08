#!/usr/bin/env bash
# Read current service evidence only. No POST, hook, start, stop, restart or patch.
set -u
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 2
V41_SOCKET=${V41_SOCKET:-unix:///run/docker-v41.sock}
V41_CONTAINER=${V41_CONTAINER:-dsv41-smoke-256k}
PY=${PY:-python3}
CONTAINER_PY=${CONTAINER_PY:-/usr/local/python3.12.13/bin/python3}
ASCEND_ROOT=${ASCEND_ROOT:-/vllm-workspace/vllm-ascend/vllm_ascend}
mkdir -p outputs/live-acceptance
OUT=$(mktemp -d "$PWD/outputs/live-acceptance/v41-metrics-review-$(date +%Y%m%d-%H%M%S)-XXXXXX") || exit 2
printf '%s\n' "$OUT" > "$OUT/output_path.txt"

record() {
    local name=$1
    shift
    "$@" > "$OUT/$name" 2> "$OUT/$name.stderr"
    local code=$?
    printf '%s\t%s\n' "$name" "$code" >> "$OUT/exit_codes.tsv"
    return 0
}

date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/metrics_started_at_utc.txt"
record metrics.prom curl -fsS --max-time 15 http://127.0.0.1:8800/metrics
date -u +%Y-%m-%dT%H:%M:%SZ > "$OUT/metrics_finished_at_utc.txt"
record models.json curl -fsS --max-time 15 http://127.0.0.1:8800/v1/models
record container_command.txt docker -H "$V41_SOCKET" inspect "$V41_CONTAINER" \
    --format 'Status={{.State.Status}} StartedAt={{.State.StartedAt}} Image={{.Image}} Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}'
# Arguments can contain internal paths: private output, do not publish unreviewed.
record processes.txt docker -H "$V41_SOCKET" exec "$V41_CONTAINER" /bin/sh -c \
    'ps -eo pid,ppid,args | grep -E "[v]llm|[a]pi_server|[E]ngineCore"'
record kv_source_snapshot.json docker -H "$V41_SOCKET" exec -i -e AGENTMON_VLLM_P2_ENABLE=0 \
    "$V41_CONTAINER" "$CONTAINER_PY" -I - --source-root /vllm-workspace/vllm/vllm \
    --ascend-root "$ASCEND_ROOT" < agentmon/scripts/dsv41_kv_source_snapshot.py
record request_source_snapshot.json docker -H "$V41_SOCKET" exec -i -e AGENTMON_VLLM_P2_ENABLE=0 \
    "$V41_CONTAINER" "$CONTAINER_PY" -I - --source-root /vllm-workspace/vllm/vllm \
    < agentmon/scripts/dsv41_request_discovery.py
record metrics_review.log "$PY" agentmon/scripts/dsv41_metrics_review.py \
    --metrics "$OUT/metrics.prom" --output-dir "$OUT/metrics_review" \
    --provenance SERVER_READONLY_CURL --collected-at-utc "$(cat "$OUT/metrics_started_at_utc.txt")"
record source_render.log "$PY" agentmon/scripts/dsv41_kv_source_snapshot.py \
    --render-snapshot "$OUT/kv_source_snapshot.json" --output-dir "$OUT/source_inventory"
record request_render.log "$PY" agentmon/scripts/dsv41_request_discovery.py \
    --render-snapshot "$OUT/request_source_snapshot.json" --output-dir "$OUT/request_inventory"
printf 'Evidence saved: %s\n' "$OUT"
cat "$OUT/exit_codes.tsv"
# Do not call this a live acceptance PASS. Any failed evidence command remains visible.
if awk '$2 != 0 { failed=1 } END { exit !failed }' "$OUT/exit_codes.tsv"; then
    exit 2
fi
exit 0
