import pytest

from vetter.llm import client
from vetter.llm.fixtures import FixtureTransport


@pytest.fixture(autouse=True)
def fixture_transport(monkeypatch):
    monkeypatch.setattr(
        client, "_transport", FixtureTransport("tests/fixtures/responses")
    )
