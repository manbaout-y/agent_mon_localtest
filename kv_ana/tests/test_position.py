import numpy as np

from kvreuse_b5.data.synthetic import rope_fixture
from kvreuse_b5.methods.position import apply_rope_shift


def test_rope_synthetic_shift_and_inverse():
    raw, encoded_source, encoded_target, metadata, source, target = rope_fixture()
    moved = apply_rope_shift(encoded_source, source, target, metadata)
    assert np.allclose(moved, encoded_target, atol=1e-10)
    recovered = apply_rope_shift(moved, target, source, metadata)
    assert np.allclose(recovered, encoded_source, atol=1e-10)

