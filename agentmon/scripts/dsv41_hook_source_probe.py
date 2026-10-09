#!/usr/bin/env python3
"""Extract the custom runtime's Hook source evidence without importing vLLM.

Works from a ZIP/copy workspace: host Git is neither needed nor invoked.
No POST, monkeypatch, package installation, privilege escalation, or restart.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


PROGRAM = r'''
import ast, hashlib, importlib.metadata as md, json, subprocess, sys
from pathlib import Path

options = json.loads(sys.argv[1])
root = Path('/vllm-workspace/vllm/vllm').resolve()
expected = {'vllm': '0.27.1+empty', 'vllm-ascend': '0.1.dev5097+ge43cf1e9f'}
versions = {}
for package in expected:
    try:
        versions[package] = md.version(package)
    except md.PackageNotFoundError:
        versions[package] = None

targets = [
 ('entrypoints/openai/chat_completion/serving.py', 'OpenAIServingChat.create_chat_completion', 'ids'),
 ('entrypoints/openai/serving_engine.py', 'OpenAIServing._base_request_id', 'ids'),
 ('v1/engine/input_processor.py', 'InputProcessor.assign_request_id', 'ids'),
 ('v1/engine/async_llm.py', 'AsyncLLM.add_request', 'ids'),
 ('v1/engine/core.py', 'EngineCore.add_request', 'scheduler'),
 ('v1/request.py', 'Request.from_engine_core_request', 'ids'),
 ('v1/core/sched/scheduler.py', 'Scheduler.add_request', 'scheduler'),
 ('v1/core/sched/scheduler.py', 'Scheduler.schedule', 'scheduler'),
 ('v1/core/sched/scheduler.py', 'Scheduler.finish_requests', 'scheduler'),
 ('v1/core/sched/scheduler.py', 'Scheduler._free_request', 'scheduler'),
 ('v1/core/kv_cache_manager.py', 'KVCacheManager.get_computed_blocks', 'kv'),
 ('v1/core/kv_cache_manager.py', 'KVCacheManager.allocate_slots', 'kv'),
 ('v1/core/kv_cache_manager.py', 'KVCacheManager.free', 'kv'),
 ('v1/core/kv_cache_manager.py', 'KVCacheManager.get_blocks', 'kv'),
 ('v1/core/kv_cache_manager.py', 'KVCacheBlocks.get_block_ids', 'kv'),
]

def named_nodes(tree):
    found = {}
    def visit(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                found[name] = child
                visit(child, name + '.')
            else:
                visit(child, prefix)
    visit(tree)
    return found

def signature(node):
    a = node.args
    values = [(v.arg, 'POSITIONAL_ONLY') for v in a.posonlyargs]
    values += [(v.arg, 'POSITIONAL_OR_KEYWORD') for v in a.args]
    if a.vararg: values.append((a.vararg.arg, 'VAR_POSITIONAL'))
    values += [(v.arg, 'KEYWORD_ONLY') for v in a.kwonlyargs]
    if a.kwarg: values.append((a.kwarg.arg, 'VAR_KEYWORD'))
    return values

def git_head(path):
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

manifest = {'kind': 'STATIC_SOURCE_ONLY', 'loaded_engine_modules_verified': False,
            'versions': versions, 'expected_versions_match': versions == expected,
            'source_git': {'vllm': git_head(root.parent),
                           'ascend': git_head(Path('/vllm-workspace/vllm-ascend'))},
            'targets': [], 'semantic_review_complete': False}
cache = {}
chunks = []
for relative, name, focus in targets:
    if options['function'] and name != options['function']: continue
    if not options['function'] and options['focus'] not in ('all', focus): continue
    path = (root / relative).resolve()
    if not path.is_relative_to(root): raise SystemExit('INVALID_SOURCE_PATH')
    if relative not in cache:
        if not path.is_file():
            cache[relative] = None
        else:
            raw = path.read_bytes()
            source = raw.decode('utf-8')
            cache[relative] = (source, ast.parse(source), hashlib.sha256(raw).hexdigest())
    item = cache[relative]
    if item is None:
        manifest['targets'].append({'path': str(path), 'function': name, 'status': 'NOT_FOUND'})
        continue
    source, tree, file_hash = item
    node = named_nodes(tree).get(name)
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        manifest['targets'].append({'path': str(path), 'function': name, 'status': 'NOT_FOUND',
                                    'available_classes': [n.name for n in tree.body if isinstance(n, ast.ClassDef)]})
        continue
    lines = source.splitlines()
    fragment = '\n'.join(lines[node.lineno - 1:node.end_lineno])
    record = {'path': str(path), 'function': name, 'status': 'FOUND',
              'start_line': node.lineno, 'end_line': node.end_lineno,
              'signature': signature(node), 'async': isinstance(node, ast.AsyncFunctionDef),
              'decorators': [ast.unparse(v) for v in node.decorator_list],
              'return_annotation': ast.unparse(node.returns) if node.returns else None,
              'file_sha256': file_hash,
              'function_sha256': hashlib.sha256(fragment.encode()).hexdigest()}
    manifest['targets'].append(record)
    # Include the COMPLETE executable body with ORIGINAL line numbers.
    # Remove only docstrings, comments and blank lines, never code statements.
    omitted = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub.body:
            first = sub.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                omitted.update(range(first.lineno, first.end_lineno + 1))
    body = ['\nPATH: '+str(path), 'FUNCTION: '+name,
            'RANGE: '+str(node.lineno)+'-'+str(node.end_lineno),
            'FORMAT: complete executable source; only docstrings/comments/blank lines omitted']
    for line_no in range(node.lineno, node.end_lineno + 1):
        line = lines[line_no - 1]
        if line_no in omitted or not line.strip() or line.lstrip().startswith('#'): continue
        body.append(str(line_no)+': '+line)
    chunks.append('\n'.join(body))

# Additional small declarations expose the real object layout, not guessed fields.
if options['focus'] in ('all', 'kv', 'ids'):
    for relative, class_name in [('v1/core/kv_cache_manager.py', 'KVCacheBlocks'),
                                  ('v1/engine/__init__.py', 'EngineCoreRequest'),
                                  ('v1/request.py', 'Request')]:
        path = root / relative
        if not path.is_file(): continue
        source = path.read_text(encoding='utf-8')
        node = named_nodes(ast.parse(source)).get(class_name)
        if not isinstance(node, ast.ClassDef): continue
        declarations = ['\nDECLARATIONS: '+str(path)+' :: '+class_name]
        for sub in node.body:
            if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                declarations.append(str(sub.lineno)+': '+ast.unparse(sub))
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == '__init__':
                declarations.append(str(sub.lineno)+': __init__ parameters='+json.dumps(signature(sub)))
                for statement in ast.walk(sub):
                    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        left = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                        if any(isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == 'self' for v in left):
                            declarations.append(str(statement.lineno)+': '+ast.unparse(statement))
        chunks.append('\n'.join(declarations))

header = ['DSV41_HOOK_SOURCE_EVIDENCE_V1',
          'READ_ONLY: no vLLM/torch import, no POST, no hook injection, no service change',
          'STATIC_ONLY: disk source is NOT proof of modules already loaded by the running EngineCore',
          'SOURCE_MANIFEST_BEGIN', json.dumps(manifest, ensure_ascii=False, separators=(',', ':')),
          'SOURCE_MANIFEST_END']
output = '\n'.join(header + chunks) + '\n'
if len(output.encode('utf-8')) > 20480:
    raise SystemExit('SOURCE_SELECTION_EXCEEDS_20KB; rerun with --focus kv/ids/scheduler or --function exact.name; no evidence was silently truncated')
print(output, end='')
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--focus', choices=('all', 'ids', 'kv', 'scheduler'), default='kv')
    parser.add_argument('--function', help='one exact Class.method from the source target list')
    parser.add_argument('--socket', default='unix:///run/docker-v41.sock')
    parser.add_argument('--container', default='dsv41-smoke-256k')
    parser.add_argument('--container-python', default='/usr/local/python3.12.13/bin/python3')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('output already exists; preserve previous evidence and select a new filename')
    command = [
        'docker', '-H', args.socket, 'exec', '-i', args.container,
        'env', '-u', 'AGENTMON_VLLM_P2_ENABLE', '-u', 'AGENTMON_VLLM_0271_TRACE_ENABLE',
        args.container_python, '-I', '-', json.dumps({'focus': args.focus, 'function': args.function}),
    ]
    result = subprocess.run(command, input=PROGRAM, capture_output=True, text=True,
                            encoding='utf-8', timeout=60)
    if result.returncode:
        raise SystemExit('READ_ONLY_PROBE_FAILED: ' + result.stderr[-2000:])
    data = result.stdout.encode('utf-8')
    if len(data) > 20480 or not data.startswith(b'DSV41_HOOK_SOURCE_EVIDENCE_V1'):
        raise SystemExit('INVALID_OR_OVERSIZED_PROBE_OUTPUT; nothing saved')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as handle:
        handle.write(data)
    print('Saved STATIC_ONLY source evidence:', args.output.resolve(), 'bytes=', len(data))


if __name__ == '__main__':
    main()
