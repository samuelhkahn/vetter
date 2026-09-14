# src/vetter/llm/fixtures.py
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from vetter.llm.client import Transport, _sha


class MissingFixture(Exception):
    pass


class FixtureTransport:
    def __init__(self, directory: str):
        self.dir = Path(directory)  # directory where fixture files are stored
        self.dir.mkdir(parents=True, exist_ok=True)
        self.record = os.getenv("VETTER_RECORD") == "1"  # read VETTER_RECORD once
        self.last_was_cached = False

    def send(self, url, payload, timeout) -> dict:
        key = _sha(payload)
        path = self.dir / f"{key}.json"
        if path.exists():  # if the path exsits load the json and return the loaded file as the response
            with open(path, "r") as f:
                response = json.load(f)["response"]
            self.last_was_cached = True
            return response
        if self.record:  # if vetter record is set then send the request to the LLM and save the response to a file in the fixture directory
            response = Transport().send(url, payload, timeout)
            with open(path, "w") as f:
                json.dump(
                    {
                        "request": payload,
                        "response": response,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    },
                    f,
                )
            self.last_was_cached = False
            return response
        raise MissingFixture(
            f"Missing fixture for key {key}. Set VETTER_RECORD to record."
        )
