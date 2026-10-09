"""Safety/format tests for the STATIC source reader, not Hook/model acceptance."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_probe():
    path = Path(__file__).resolve().parents[2] / 'scripts/dsv41_hook_source_probe.py'
    spec = importlib.util.spec_from_file_location('hook_source_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_embedded_program_compiles_and_never_imports_model():
    import ast
    probe = load_probe()
    tree = ast.parse(probe.PROGRAM)
    compile(tree, '<static-source-reader>', 'exec')
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imported += [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
    assert not any(n and (n.startswith('vllm') or n.startswith('torch')) for n in imported)
    assert 'requests' not in imported


def test_copy_workspace_no_git_and_readonly_docker_command(tmp_path, monkeypatch):
    probe = load_probe()
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='DSV41_HOOK_SOURCE_EVIDENCE_V1\nSTATIC_ONLY\n', stderr='')
    monkeypatch.setattr(probe.subprocess, 'run', run)
    output = tmp_path / 'evidence.txt'
    probe.main(['--output', str(output), '--focus', 'kv'])
    command, kwargs = calls[0]
    assert command[:6] == ['docker', '-H', 'unix:///run/docker-v41.sock', 'exec', '-i', 'dsv41-smoke-256k']
    assert '-I' in command
    assert command[6:8] == ['env', '-u']
    assert json.loads(command[-1]) == {'focus': 'kv', 'function': None}
    assert kwargs['input'] == probe.PROGRAM
    assert len(calls) == 1
    assert output.read_text().startswith('DSV41_HOOK_SOURCE_EVIDENCE_V1')
    with pytest.raises(SystemExit):
        probe.main(['--output', str(output)])
    assert len(calls) == 1


@pytest.mark.parametrize('returncode,stdout,stderr', [
    (1, '', 'Permission denied'),
    (0, 'unexpected output', ''),
    (0, 'DSV41_HOOK_SOURCE_EVIDENCE_V1' + 'x' * 20480, ''),
])
def test_bad_evidence_never_written(tmp_path, monkeypatch, returncode, stdout, stderr):
    probe = load_probe()
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr))
    output = tmp_path / 'no.txt'
    with pytest.raises(SystemExit):
        probe.main(['--output', str(output)])
    assert not output.exists()
