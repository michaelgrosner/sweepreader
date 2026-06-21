import pytest
import textwrap
import tempfile
from pathlib import Path

from sweepreader.config import load_config


def write_config(text: str) -> Path:
    t = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    t.write(textwrap.dedent(text))
    t.close()
    return Path(t.name)


MINIMAL = """
    model: "anthropic/claude-haiku-4-5"
    suppress_threshold: 35
    trailing_days: 14
    profile_prompt: "test"
    tier_weights: {A: 1.0, B: 0.85, C: 0.55, D: 0.40, E: 0.10}
    sources:
      - id: test_src
        modality: rss
        endpoint: "https://example.com/rss"
        default_tier_hint: B
        weight: 1.0
        parse: rss_generic
"""


def test_load_valid_config():
    cfg = load_config(write_config(MINIMAL))
    assert cfg.model == "anthropic/claude-haiku-4-5"
    assert cfg.suppress_threshold == 35
    assert len(cfg.sources) == 1
    assert cfg.sources[0].id == "test_src"
    assert cfg.tier_weights["A"] == 1.0


def test_missing_key_raises():
    bad = """
    suppress_threshold: 35
    trailing_days: 14
    profile_prompt: "test"
    tier_weights: {A: 1.0, B: 0.85, C: 0.55, D: 0.40, E: 0.10}
    sources: []
    """
    with pytest.raises(ValueError, match="missing required key 'model'"):
        load_config(write_config(bad))


def test_invalid_threshold_raises():
    bad = MINIMAL.replace("suppress_threshold: 35", "suppress_threshold: 150")
    with pytest.raises(ValueError, match="suppress_threshold"):
        load_config(write_config(bad))


def test_missing_tier_weight_raises():
    bad = MINIMAL.replace("tier_weights: {A: 1.0, B: 0.85, C: 0.55, D: 0.40, E: 0.10}", "tier_weights: {A: 1.0}")
    with pytest.raises(ValueError, match="tier_weights missing tiers"):
        load_config(write_config(bad))


def test_duplicate_source_id_raises():
    dup = MINIMAL + """
      - id: test_src
        modality: api
        endpoint: "https://example.com/api"
        default_tier_hint: D
        weight: 1.0
        parse: federal_register
"""
    with pytest.raises(ValueError, match="Duplicate source id"):
        load_config(write_config(dup))


def test_invalid_modality_raises():
    bad = MINIMAL.replace("modality: rss", "modality: ftp")
    with pytest.raises(ValueError, match="invalid modality"):
        load_config(write_config(bad))


def test_config_hash_is_stable():
    cfg = load_config(write_config(MINIMAL))
    assert cfg.config_hash() == cfg.config_hash()
    assert len(cfg.config_hash()) == 16
