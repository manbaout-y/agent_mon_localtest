#!/usr/bin/env bash

# This wrapper delegates to the site's existing, unchanged start.BASELINE.sh.
# DSV4_BASELINE_START may point elsewhere when the baseline launcher is not in
# the same directory as this bundle.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASELINE_START=${DSV4_BASELINE_START:-$SCRIPT_DIR/start.BASELINE.sh}

if [ ! -f "$BASELINE_START" ]; then
  echo "start.INTERVENTION.sh: baseline launcher not found: $BASELINE_START" >&2
  echo "Set DSV4_BASELINE_START to the existing server start.BASELINE.sh." >&2
  exit 1
fi

export DSV4_STATE_INTERVENTION=${DSV4_STATE_INTERVENTION:-off}

if [ "$DSV4_STATE_INTERVENTION" != "off" ]; then
  : "${DSV4_STATE_INTERVENTION_SPEC:?required for non-off intervention mode}"
  : "${DSV4_STATE_INTERVENTION_TRACE:?required for non-off intervention mode}"
  if [ -z "${DSV4_STATE_INTERVENTION_TAG_FILE:-${DSV4_KV_TAG_FILE:-}}" ]; then
    echo "start.INTERVENTION.sh: a tag file variable is required for non-off mode" >&2
    exit 1
  fi
fi

exec bash "$BASELINE_START" "$@"

