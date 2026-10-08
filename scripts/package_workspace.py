#!/usr/bin/env python3
"""Package current workspace without outputs, environments or raw data."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists; choose a new filename')
    spec = importlib.util.spec_from_file_location('source_bundle', ROOT / 'kv_ana/scripts/build_complete_source_bundle.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    migration = json.loads((ROOT / 'BUILD_MANIFEST.json').read_text())
    files = {}
    for label, items in migration['selections'].items():
        files.update({f'{label}/{name}': path for name, path in builder.collect(ROOT / label, items).items()})
    files.update(builder.collect(ROOT, ['README.md', 'AGENTS.md', '.gitignore', 'docs', 'scripts',
                                       'integration', 'BUILD_MANIFEST.json', 'SOURCE_TREE.txt']))
    generated = (json.dumps({'workspace': 'hetero_state_lab', 'files': sorted(files),
                            'models_or_data_included': False,
                            'source_kind': 'current unified working tree'}, indent=2) + '\n').encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, 'w:gz') as tar:
        for name, path in sorted(files.items()):
            tar.add(path, arcname=f'hetero_state_lab/{name}', recursive=False)
        info = tarfile.TarInfo('hetero_state_lab/PACKAGE_MANIFEST.json')
        info.size = len(generated)
        tar.addfile(info, io.BytesIO(generated))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name + '.sha256').write_text(f'{digest}  {args.output.name}\n')
    print(json.dumps({'bundle': str(args.output.resolve()), 'bytes': args.output.stat().st_size,
                      'files': len(files) + 1, 'sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
