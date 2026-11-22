"""Tests de scripts/depassements.py : la comparaison permis/interdits, sans simuler."""

import dataclasses
import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import SimConfig, load_scenario

_SPEC = importlib.util.spec_from_file_location(
    "depassements", Path(__file__).parent.parent / "scripts" / "depassements.py"
)
dp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dp)


@pytest.mark.parametrize("scenario", dp.SCENARIOS)
def test_seule_l_option_de_depassement_change(scenario):
    defaut = SimConfig.from_scenario(load_scenario(scenario))
    for depassements, _ in dp.HYPOTHESES:
        config = dp.configuration(scenario, depassements)
        assert config.depassements_autorises is depassements
        assert dataclasses.replace(config, depassements_autorises=defaut.depassements_autorises) \
            == defaut


def test_les_scenarios_sont_endogenes():
    for scenario in dp.SCENARIOS:
        config = SimConfig.from_scenario(load_scenario(scenario))
        assert config.politique_approvisionnement == "point_de_commande"


def test_la_difference_est_interdits_moins_permis_et_appariee():
    series = {"phase2_endogene": {"permis": [0.99, 0.98, 0.97],
                                  "interdits": [0.97, 0.96, 0.95]}}
    a = dp.analyser(series)["phase2_endogene"]
    assert a["difference"].mean == pytest.approx(-0.02)
    assert a["difference"].std == pytest.approx(0.0)  # écart constant : apparié, IC nul
    assert a["significative"]
