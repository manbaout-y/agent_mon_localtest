#!/usr/bin/env bash

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MAX_DIFF_LINES=150
KEYWORDS='compressed_kv|compressor|dsa_kv_compress_scatter|compress_kv_cache|capture|dump|save|C4|C128|KV_CAPTURE|DSA_CAPTURE|COMPRESS.*CAPTURE'
OVERALL_STATUS=PASS

compare_file() {
  label=$1
  official_rel=$2
  installed=$3
  known_server_sha=$4
  official="$SCRIPT_DIR/$official_rel"

  echo "FILE=$label"
  echo "OFFICIAL_FILE=$official_rel"
  echo "INSTALLED_FILE=$installed"
  if [ ! -f "$official" ]; then
    echo "STATUS=OFFICIAL_FILE_MISSING"
    OVERALL_STATUS=MISMATCH
    return
  fi
  if [ ! -f "$installed" ]; then
    echo "STATUS=INSTALLED_FILE_MISSING"
    OVERALL_STATUS=MISMATCH
    return
  fi

  official_sha=$(sha256sum "$official" | awk '{print $1}')
  installed_sha=$(sha256sum "$installed" | awk '{print $1}')
  official_lines=$(wc -l < "$official" | tr -d ' ')
  installed_lines=$(wc -l < "$installed" | tr -d ' ')
  official_bytes=$(wc -c < "$official" | tr -d ' ')
  installed_bytes=$(wc -c < "$installed" | tr -d ' ')

  echo "OFFICIAL_SHA256=$official_sha"
  echo "INSTALLED_SHA256=$installed_sha"
  echo "KNOWN_SERVER_SHA256=$known_server_sha"
  echo "OFFICIAL_LINES=$official_lines"
  echo "INSTALLED_LINES=$installed_lines"
  echo "OFFICIAL_BYTES=$official_bytes"
  echo "INSTALLED_BYTES=$installed_bytes"
  echo "INSTALLED_KEYWORD_SCAN_BEGIN"
  grep -nEi "$KEYWORDS" "$installed" | head -n 40 || true
  echo "INSTALLED_KEYWORD_SCAN_END"

  if [ "$official_sha" = "$installed_sha" ]; then
    echo "STATUS=MATCH"
    echo "DIFF_ADDED_LINES=0"
    echo "DIFF_REMOVED_LINES=0"
    return
  fi

  OVERALL_STATUS=MISMATCH
  echo "STATUS=MISMATCH"
  diff_file=$(mktemp)
  diff -U 5 "$official" "$installed" > "$diff_file"
  added=$(awk '/^\+\+\+/{next} /^\+/{count++} END{print count+0}' "$diff_file")
  removed=$(awk '/^---/{next} /^-/{count++} END{print count+0}' "$diff_file")
  diff_lines=$(wc -l < "$diff_file" | tr -d ' ')
  echo "DIFF_ADDED_LINES=$added"
  echo "DIFF_REMOVED_LINES=$removed"
  echo "DIFF_OUTPUT_LINES=$diff_lines"

  if [ "$diff_lines" -le "$MAX_DIFF_LINES" ]; then
    echo "UNIFIED_DIFF_BEGIN"
    cat "$diff_file"
    echo "UNIFIED_DIFF_END"
  else
    echo "UNIFIED_DIFF_TRUNCATED=true"
    echo "CHANGED_HUNK_RANGES_BEGIN"
    grep '^@@' "$diff_file" | head -n 60 || true
    echo "CHANGED_HUNK_RANGES_END"
    echo "CHANGED_KEYWORD_LINES_BEGIN"
    grep -nEi "$KEYWORDS" "$diff_file" | head -n 60 || true
    echo "CHANGED_KEYWORD_LINES_END"
  fi

  echo "OFFICIAL_KEYWORD_SCAN_BEGIN"
  grep -nEi "$KEYWORDS" "$official" | head -n 40 || true
  echo "OFFICIAL_KEYWORD_SCAN_END"
  rm -f "$diff_file"
}

cd "$SCRIPT_DIR" || exit 2
echo "OFFICIAL_BUNDLE_CHECKSUM_BEGIN"
sha256sum -c SHA256SUMS
checksum_status=$?
echo "OFFICIAL_BUNDLE_CHECKSUM_END"
if [ "$checksum_status" -ne 0 ]; then
  echo "OFFICIAL_BUNDLE_STATUS=CORRUPT"
  exit 2
fi

compare_file \
  vllm_ascend_attention_dsa_v1 \
  official/vllm-ascend/vllm_ascend/attention/dsa_v1.py \
  /data/node0_disk1/liguowei/AK-Infer-Lab/.conda/envs/ak-infer-lab-vllm-ascend0.22.1rc1/lib/python3.11/site-packages/vllm_ascend/attention/dsa_v1.py \
  99903d816a1967634f9684dd098088fdeb4711c64324739a3a0b8a7b2a4df9f2

compare_file \
  vllm_ascend_models_deepseek_v4 \
  official/vllm-ascend/vllm_ascend/models/deepseek_v4.py \
  /data/node0_disk1/liguowei/AK-Infer-Lab/.conda/envs/ak-infer-lab-vllm-ascend0.22.1rc1/lib/python3.11/site-packages/vllm_ascend/models/deepseek_v4.py \
  9398e49d7206ba5a62629409405be057318e0657e40a25cf15c43304f78d01a4

compare_file \
  vllm_deepseek_v4_compressor \
  official/vllm/vllm/models/deepseek_v4/compressor.py \
  /data/node0_disk1/vllm-0.22.1/vllm/models/deepseek_v4/compressor.py \
  fdbf933e2078d30572c07f0e5c6f76dbc8ba53e2c2f24f0fb93a1f9adf109cd8

echo "RUNTIME_BASELINE_MATCH=$OVERALL_STATUS"
if [ "$OVERALL_STATUS" = PASS ]; then
  exit 0
fi
exit 1
