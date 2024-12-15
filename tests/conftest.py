"""Shared pytest fixtures and configuration."""

import pytest
from pathlib import Path
from typing import Dict, Any
import yaml

@pytest.fixture
def mock_config() -> Dict[str, Any]:
    """Load the mock configuration file."""
    config_path = Path(__file__).parent / "mock" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

@pytest.fixture
def mock_config_tz() -> Dict[str, Any]:
    """Load the mock configuration file with timezone."""
    config_path = Path(__file__).parent / "mock" / "config_tz.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

@pytest.fixture
def test_data_dir() -> Path:
    """Return path to test data directory."""
    return Path(__file__).parent / "mock"
