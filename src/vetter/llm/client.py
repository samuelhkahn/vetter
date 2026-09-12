# Client for interacting with the LLM models defined in the release configuration.
import functools
import hashlib
import json
import time

import httpx
import yaml
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

tracer = trace.get_tracer(__name__)
RELEASE_PATH = "release.yaml"
DEFAULT_TIMEOUT_S = 60.0

# ---------- configuration ----------


class ModelConfig(BaseModel):
    provider: str  # model provider format e.g. openai API style
    model: str  # the actual model e.g. gpt 5-6-sol, qwe-4b, etc...
    base_url: str  # the URL to acces the models API


class RealBogusCut(BaseModel):
    drb_min: float  # minimum threshold for bogus threshold from ZTF, e.g. only keep object above this
    isdiffpos: bool  # whether the detection is positive in the difference image
    nbad_max: int  # maximum number of bad pixels allowed


class Release(BaseModel):
    seed: int  # the seed used for this release
    release_id: str  # the release ID
    real_bogus_cut: RealBogusCut  # the cut used in this release
    models: dict[str, ModelConfig]  # the model config
    prompt_hashes: dict[str, str]  # the hases of the prompts used in this release
    tool_schema_hash: str  # hash of the schema
    astroalertbench: dict[str, str]  # what astro alert bnech we used for this release


class Completion(BaseModel):
    text: str | None  # the completion
    tool_calls: list[dict] = []  # the tool calls made
    model: str  # the actual model
    input_tokens: int | None  # the number of input tokens used
    output_tokens: int | None  # the number of output tokens generated
    latency_ms: float


# Load the release configuration from the YAML file and cache it for future use. The release ID is computed as the SHA256 hash of the raw YAML content.
@functools.lru_cache(maxsize=1)
def load_release(path: str = RELEASE_PATH) -> Release:
    with open(path, "rb") as f:
        raw = f.read()

    data = yaml.safe_load(raw)
    data["release_id"] = hashlib.sha256(raw).hexdigest()
    return Release(**data)


# ---------- results and errors ----------
class ClientError(Exception):
    """Base for every error complete() raises."""


class LLMTimeout(ClientError):
    pass


class LLMHTTPError(ClientError):
    pass


class BadResponse(ClientError):
    pass


# ---------- transport: the seam tests replace ----------
# the Transport layer sends requests to LLMs
class Transport:
    def send(self, url: str, payload: dict, timeout: float) -> dict:
        return httpx.post(url, json=payload, timeout=timeout).raise_for_status().json()


# SHA the object
def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


_transport = Transport()  # modules level transport instance, can be replaced in tests


# ---------- the main entry point ----------
def complete(role, messages, tools=None) -> Completion:
    release = load_release()
    cfg = release.models[role]
    # configure the payload
    payload = {"model": cfg.model, "messages": messages}
    if tools is not None:
        payload["tools"] = tools
    # Start tracing span for the LLM completion
    with tracer.start_as_current_span("llm.complete") as span:
        span.set_attribute("vetter.role", role)
        span.set_attribute("vetter.release_id", release.release_id)
        span.set_attribute("vetter.prompt_hash", _sha(payload))
        span.set_attribute("vetter.cache", "miss")
        span.set_attribute("gen_ai.system", cfg.provider)
        span.set_attribute("gen_ai.request.model", cfg.model)
        t0 = time.perf_counter()
        try:
            raw = _transport.send(
                f"{cfg.base_url}/chat/completions", payload, DEFAULT_TIMEOUT_S
            )  # send the request to the LLM
        # specific before general: both TimeoutException and HTTPStatusError descend from HTTPError
        except httpx.TimeoutException as e:
            span.set_status(StatusCode.ERROR, "timeout")
            raise LLMTimeout(f"{role}: no response in {DEFAULT_TIMEOUT_S}s") from e
        except httpx.HTTPStatusError as e:
            span.set_status(StatusCode.ERROR, f"http {e.response.status_code}")
            raise LLMHTTPError(f"{role}: http {e.response.status_code}") from e
        except httpx.HTTPError as e:
            span.set_status(StatusCode.ERROR, "connection")
            raise LLMHTTPError(f"{role}: {e}") from e

        latency_ms = (time.perf_counter() - t0) * 1000

        try:
            msg = raw["choices"][0]["message"]  # extract the messages first choice
            usage = raw.get("usage") or {}
            # create a completion object from the response
            result = Completion(
                text=msg.get("content"),
                tool_calls=msg.get("tool_calls") or [],
                model=raw.get("model", cfg.model),
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                latency_ms=latency_ms,
            )
        except (KeyError, IndexError, TypeError) as e:
            span.set_status(StatusCode.ERROR, "bad response")
            raise BadResponse(f"{role}: unexpected response shape") from e

        span.set_attribute("gen_ai.response.model", result.model)
        span.set_attribute("vetter.latency_ms", latency_ms)
        if result.input_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
        if result.output_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
        span.set_status(StatusCode.OK)
        return result


if __name__ == "__main__":
    from vetter.llm.telemetry import configure_tracing

    configure_tracing()
    try:
        print(
            complete("triage", [{"role": "user", "content": "Reply with the word ok."}])
        )
    except ClientError as e:
        print("client error:", e)
