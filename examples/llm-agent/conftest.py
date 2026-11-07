import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "tests" / "fixtures"


@pytest.fixture
def fixture_json():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    return load
