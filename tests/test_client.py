import json

import pytest

from vetter.llm import client
from vetter.llm.client import BadResponse, Completion, _sha, complete
from vetter.llm.fixtures import FixtureTransport, MissingFixture


def test_cached_call_returns_completion():
    result = complete(
        "triage", [{"role": "user", "content": "Reply with the word ok."}]
    )
    assert isinstance(result, Completion)
    assert result.text


# unrecored call fails


def test_unrecorded_call_fails():
    with pytest.raises(MissingFixture) as info:
        complete(
            "triage",
            [{"role": "user", "content": "This is gonna fail since it isn't cached"}],
        )
    assert "Missing fixture for key" in str(info.value)


def test_malformed_fixture_raises_bad_response(tmp_path, monkeypatch):
    messages = [{"role": "user", "content": "broken"}]
    payload = {
        "model": client.load_release().models["triage"].model,
        "messages": messages,
    }
    (tmp_path / f"{_sha(payload)}.json").write_text(
        json.dumps({"response": {"choices": []}})
    )
    monkeypatch.setattr(client, "_transport", FixtureTransport(str(tmp_path)))
    with pytest.raises(BadResponse):
        client.complete("triage", messages)
