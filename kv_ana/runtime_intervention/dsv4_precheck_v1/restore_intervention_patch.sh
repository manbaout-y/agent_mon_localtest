#!/usr/bin/env bash

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
TARGET=${DSV4_DSA_TARGET:-/data/node0_disk1/liguowei/AK-Infer-Lab/.conda/envs/ak-infer-lab-vllm-ascend0.22.1rc1/lib/python3.11/site-packages/vllm_ascend/attention/dsa_v1.py}

exec "$PYTHON_BIN" "$SCRIPT_DIR/patch_runtime.py" restore --target "$TARGET"

