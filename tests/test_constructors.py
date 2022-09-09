"""Test constructor module."""
from pathlib import Path

import numpy as np
import yaml
from numpy.testing import assert_almost_equal

from pygenstability import constructors

CONSTRUCTORS = [
    "linearized",
    "continuous_combinatorial",
    "continuous_normalized",
    "signed_modularity",
    "directed",
]
DATA = Path(__file__).absolute().parent / "data"


def _list_data(data):
    data["quality"] = np.array(data["quality"].toarray(), dtype=float).tolist()
    data["null_model"] = np.array(data["null_model"], dtype=float).tolist()
    return data


def test_load_constructor(graph):
    for constr in CONSTRUCTORS:
        data = _list_data(constructors.load_constructor(constr, graph).get_data(1))
        yaml.dump(data, open(DATA / f"test_constructor_{constr}.yaml", "w"))
        expected_data = yaml.safe_load(open(DATA / f"test_constructor_{constr}.yaml", "r"))
        assert_almost_equal(data["quality"], expected_data["quality"])
        assert_almost_equal(data["null_model"], expected_data["null_model"])


def test_load_constructor_gap(graph):
    for constr in CONSTRUCTORS:
        data = _list_data(
            constructors.load_constructor(constr, graph, spectral_gap=True).get_data(1)
        )
        yaml.dump(data, open(DATA / f"test_constructor_{constr}_gap.yaml", "w"))
        expected_data = yaml.safe_load(open(DATA / f"test_constructor_{constr}_gap.yaml", "r"))
        assert_almost_equal(data["quality"], expected_data["quality"])
        assert_almost_equal(data["null_model"], expected_data["null_model"])
