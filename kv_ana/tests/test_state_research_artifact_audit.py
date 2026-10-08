"""Single frozen-core exception: canonical artifact provenance, not SVD mathematics."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

from kvreuse_b5.data.types import StatePair
from kvreuse_b5.execution.artifacts import ArtifactStore, _artifact_hash
from kvreuse_b5.methods.base import FitContext


def test_frozen_artifact_is_identical_across_python_hash_seeds():
    code = '''
import json,numpy as np
from kvreuse_b5.data.types import StatePair
from kvreuse_b5.execution.artifacts import ArtifactStore
from kvreuse_b5.methods.base import FitContext
pairs=[StatePair(str(i),'train','m','PREFIX_ONLY','K',0,np.zeros((2,2)),
 np.array([[i+1.,2.],[3.,i+4.]]),np.arange(2)) for i in range(6)]
store=ArtifactStore()
store.prepare_frozen(pairs,2,FitContext(frozenset(p.state_identity for p in pairs),'config'))
artifact=store.frozen_for(pairs[0],2)
print(json.dumps({'hash':artifact.artifact_hash,'basis':artifact.basis.tolist(),
 'sv':artifact.singular_values.tolist()},sort_keys=True))
'''
    root = Path(__file__).resolve().parents[1]
    outputs = []
    for seed in ("1", "2", "3"):
        result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONHASHSEED": seed})
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1] == outputs[2]


def test_canonical_provenance_keeps_fit_basis_and_audit_ids():
    pair = StatePair("p", "train", "m", "PREFIX_ONLY", "K", 0,
                     np.zeros((2, 2)), np.array([[1., 2.], [3., 4.]]), np.arange(2))
    store = ArtifactStore()
    store.prepare_frozen([pair], 2, FitContext(frozenset([pair.state_identity]), "config"))
    artifact = store.frozen_for(pair, 2)
    _, singular, vt = np.linalg.svd(pair.residual, full_matrices=False)
    assert np.array_equal(artifact.basis, vt.T)
    assert np.array_equal(artifact.singular_values, singular)
    assert artifact.fit_pair_ids == frozenset([pair.state_identity])
    assert artifact.artifact_hash == _artifact_hash(pair.fit_key, vt.T.copy(), singular,
                                                   (pair.state_identity,), "config")
