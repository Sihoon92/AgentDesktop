"""The OpenAI Responses API calls and the model objects a run uses.

gpt-6.1-terra supports tool calling only through this API. The text model runs the task; a
vision model, when one is named, reads pictures for it (`agent.vision`). Connection values come
from the environment: OPENAI_API_KEY and OPENAI_BASE_URL for the text model, VISION_API_KEY and
VISION_BASE_URL for the vision model, falling back to the text ones. A blank value counts as absent.
Messages and start lines name sources and env names only, never a model name, address or key.
"""

from dataclasses import dataclass

from agent.vision import DEFAULT_MAX_SIDE, POINT_MODES, Vision

OMIT_EFFORT = "omit"
TEXT_KEY_ENV = "OPENAI_API_KEY"
TEXT_URL_ENV = "OPENAI_BASE_URL"
VISION_MODEL_ENV = "VISION_MODEL"
VISION_KEY_ENV = "VISION_API_KEY"
VISION_URL_ENV = "VISION_BASE_URL"
IGNORED_IMAGE_MODE_NOTE = "image_mode 무시(나눈 방식)"


class ModelSetupError(Exception):
    """The connection values cannot make the models. The message is Korean and names env
    names only, never a value, an address or a key."""


class OpenAIResponder:
    def __init__(self, model, reasoning_effort, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    def respond(self, instructions, items, tools, effort=None):
        """One model call. `effort` sets the reasoning effort of this call only; None keeps the
        responder's own. An effort of OMIT_EFFORT sends no `reasoning` argument, for models that
        refuse it."""
        effort = effort or self._reasoning_effort
        arguments = {"model": self._model, "instructions": instructions, "input": items, "tools": tools,
                     "parallel_tool_calls": False}
        if effort != OMIT_EFFORT:
            arguments["reasoning"] = {"effort": effort}
        return self._client.responses.create(**arguments)


class VisionReader:
    """One picture and one question per call, no tools and no history. `reasoning` is sent only
    when an effort is set and is not OMIT_EFFORT."""

    def __init__(self, model, client, reasoning_effort=None):
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    def ask(self, instructions, question, image_url):
        """The reply text and its total tokens. Errors from the client are raised to the caller."""
        content = [{"type": "input_text", "text": question},
                   {"type": "input_image", "image_url": image_url, "detail": "high"}]
        arguments = {"model": self._model, "instructions": instructions,
                     "input": [{"role": "user", "content": content}]}
        if self._reasoning_effort and self._reasoning_effort != OMIT_EFFORT:
            arguments["reasoning"] = {"effort": self._reasoning_effort}
        response = self._client.responses.create(**arguments)
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", 0) if usage else 0
        return getattr(response, "output_text", None) or "", tokens or 0


@dataclass(frozen=True)
class Models:
    text: OpenAIResponder
    vision: Vision | None
    text_source: str
    vision_source: str | None
    image_mode_ignored: bool


def make_client(api_key, base_url):
    """An OpenAI client; `base_url` None keeps the library's default address."""
    from openai import OpenAI

    arguments = {"api_key": api_key}
    if base_url is not None:
        arguments["base_url"] = base_url
    return OpenAI(**arguments)


def vision_setup(settings, environ, make_client=make_client):
    """(Vision, source) when a vision model is named, else (None, None). The settings file's
    vision_model beats VISION_MODEL. A vision key or address with no vision model is refused,
    so a half-written setup never runs quietly in one-model mode."""
    settings = settings or {}
    name, source = _vision_model(settings, environ)
    if name is None:
        stray = [env for env in (VISION_KEY_ENV, VISION_URL_ENV) if _env(environ, env)]
        if stray:
            raise ModelSetupError(f"{', '.join(stray)}가 있지만 비전 모델 이름이 없습니다. "
                                  f"{VISION_MODEL_ENV}(또는 설정 파일의 vision_model)를 넣거나 "
                                  f"{', '.join(stray)}를 지워 주세요")
        return None, None
    api_key = _env(environ, VISION_KEY_ENV) or _env(environ, TEXT_KEY_ENV)
    if api_key is None:
        raise ModelSetupError(f"비전 모델의 키가 없습니다. {VISION_KEY_ENV}나 {TEXT_KEY_ENV}를 "
                              "환경변수나 .env에 넣어 주세요")
    base_url = _env(environ, VISION_URL_ENV) or _env(environ, TEXT_URL_ENV)
    reader = VisionReader(name, make_client(api_key, base_url), settings.get("vision_reasoning_effort"))
    vision = Vision(reader, max_side=settings.get("vision_max_side", DEFAULT_MAX_SIDE),
                    points=settings.get("vision_points", POINT_MODES[0]))
    return vision, source


def text_responder(task, environ, make_client=make_client):
    """The text model's responder for `task`, connected with OPENAI_API_KEY and OPENAI_BASE_URL."""
    api_key = _env(environ, TEXT_KEY_ENV)
    if api_key is None:
        raise ModelSetupError(f"{TEXT_KEY_ENV}가 없습니다. 환경변수나 .env에 넣어 주세요")
    client = make_client(api_key, _env(environ, TEXT_URL_ENV))
    return OpenAIResponder(task.model, task.reasoning_effort, client)


def build_models(task, settings, environ, make_client=make_client):
    """Both model objects for a run. The vision setup is checked first, so a broken one stops
    the run before any client exists for the text model."""
    vision, vision_source = vision_setup(settings, environ, make_client)
    text = text_responder(task, environ, make_client)
    ignored = vision is not None and task.image_mode != "user_message"
    return Models(text=text, vision=vision, text_source=task.model_source, vision_source=vision_source,
                  image_mode_ignored=ignored)


def start_line(models):
    """The run's start line: where each model's name came from, never the names themselves."""
    line = f"텍스트 모델: {models.text_source} · 비전 모델: {models.vision_source or '없음'}"
    if models.image_mode_ignored:
        line += f" · {IGNORED_IMAGE_MODE_NOTE}"
    return line


def _vision_model(settings, environ):
    named = settings.get("vision_model")
    if isinstance(named, str) and named.strip():
        return named.strip(), "설정 파일"
    from_env = _env(environ, VISION_MODEL_ENV)
    if from_env is not None:
        return from_env, "env"
    return None, None


def _env(environ, name):
    value = (environ or {}).get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
