#!/usr/bin/env python3
"""Orchestrate existing offline acceptance; never start a model/service."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['core', 'wire', 'all'], default='core')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error('output must be new/empty')
    output.mkdir(parents=True, exist_ok=True)
    kv, agent = ROOT / 'kv_ana', ROOT / 'agentmon'
    steps = []
    if args.suite in ('core', 'all'):
        steps = [
            ('kv_tests', kv, ['-m', 'pytest', 'tests', '-o', 'addopts=', '-q', '-p', 'no:cacheprovider']),
            ('agentmon_tests', agent, ['-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider']),
            ('framework_smoke', kv, ['-m', 'kvreuse_b5.cli.smoke', '--config', 'configs/synthetic_smoke.yaml', '--output', str(output / 'framework')]),
            ('state_smoke', kv, ['scripts/state_research_offline_smoke.py', '--output', str(output / 'state')]),
            ('recipes', kv, ['-m', 'kvreuse_b5.cli.recipes', 'list']),
            ('agentmon_cli', agent, ['-m', 'agentmon', '--help']),
            ('compile', ROOT, ['-m', 'compileall', '-q', 'scripts', 'agentmon/src', 'agentmon/scripts', 'kv_ana/src', 'kv_ana/scripts', 'kv_ana/runtime_intervention']),
        ]
    if args.suite in ('wire', 'all'):
        steps.append(('wire', kv, ['scripts/validate_v41_joint_client.py', '--agentmon-root', str(agent), '--output', str(output / 'wire')]))
    results = []
    for name, cwd, arguments in steps:
        env = {**os.environ, 'PYTHONPATH': str(cwd / 'src'),
               'AGENTMON_REPO': str(agent), 'KVREUSE_REPO': str(kv),
               'PYTHONPYCACHEPREFIX': str(output / 'pycache'),
               'MSWEA_GLOBAL_CONFIG_DIR': str(output / 'miniswe-config'),
               'LITELLM_LOCAL_MODEL_COST_MAP': 'true', 'MSWEA_SILENT_STARTUP': '1'}
        with (output / f'{name}.log').open('w') as log:
            result = subprocess.run([sys.executable, *arguments], cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        results.append({'step': name, 'exit_code': result.returncode, 'log': f'{name}.log'})
        print(f'{name}={"PASS" if result.returncode == 0 else "FAIL"}', flush=True)
        status = 'RUNNING' if len(results) < len(steps) else ('FAIL' if any(r['exit_code'] for r in results) else 'PASS')
        (output / 'report.json').write_text(json.dumps({'suite': args.suite, 'results': results,
            'status': status, 'expected_steps': len(steps),
            'model_executed': False, 'server_accessed': False}, indent=2) + '\n')
    return int(any(r['exit_code'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
