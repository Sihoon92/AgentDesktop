from types import SimpleNamespace

import pytest

from agent import settings as settings_module
from agent import vision as vision_module
from agent.llm import (ModelSetupError, OpenAIResponder, VisionReader, build_models, start_line, text_responder,
                       vision_setup)
from agent.task import Task
from agent.vision import Vision

TEXT_ENV = {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "u"}
SECRETS = ("k", "u", "vk", "vu", "text-model", "eye-model")


def recording_client():
    sent = []
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: sent.append(kwargs) or "response"))
    return client, sent


class ClientFactory:
    """Records each (api_key, base_url) and hands back a client that records its calls."""

    def __init__(self, reply=None):
        self.made = []
        self.sent = []
        self.reply = reply or SimpleNamespace(output_text="answer", usage=SimpleNamespace(total_tokens=7))

    def __call__(self, api_key, base_url):
        self.made.append((api_key, base_url))
        return SimpleNamespace(responses=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        return self.reply


def task(**overrides):
    values = {"model": "text-model", "model_source": "env", "reasoning_effort": "low"}
    values.update(overrides)
    return Task("g", r"C:\apps\Tool.exe", 3, [], **values)


def test_no_vision_model_means_one_model_mode():
    factory = ClientFactory()

    assert vision_setup({}, TEXT_ENV, make_client=factory) == (None, None)
    models = build_models(task(), {}, TEXT_ENV, make_client=factory)
    assert (models.vision, models.vision_source, models.image_mode_ignored) == (None, None, False)
    assert models.text_source == "env"
    assert factory.made == [("k", "u")]


def test_vision_model_from_env_uses_text_key_and_url():
    factory = ClientFactory()

    vision, source = vision_setup({}, {**TEXT_ENV, "VISION_MODEL": "eye-model"}, make_client=factory)

    assert isinstance(vision, Vision) and source == "env"
    assert factory.made == [("k", "u")]


def test_vision_key_and_url_override_the_text_ones():
    factory = ClientFactory()
    environ = {**TEXT_ENV, "VISION_MODEL": "eye-model", "VISION_API_KEY": "vk", "VISION_BASE_URL": "vu"}

    models = build_models(task(), {}, environ, make_client=factory)

    assert sorted(factory.made) == [("k", "u"), ("vk", "vu")]
    assert models.vision is not None


def test_missing_base_url_is_not_passed_on():
    factory = ClientFactory()

    text_responder(task(), {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "  "}, make_client=factory)

    assert factory.made == [("k", None)]


def test_settings_vision_model_beats_env():
    factory = ClientFactory()

    vision, source = vision_setup({"vision_model": "eye-model"}, {**TEXT_ENV, "VISION_MODEL": "other"},
                                  make_client=factory)
    vision.reader.ask("rules", "q", "data:image/png;base64,AA")

    assert source == "설정 파일"
    assert factory.sent[0]["model"] == "eye-model"


@pytest.mark.parametrize("name", ["VISION_BASE_URL", "VISION_API_KEY"])
def test_vision_url_without_vision_model_is_refused(name):
    with pytest.raises(ModelSetupError) as raised:
        vision_setup({}, {**TEXT_ENV, name: "vu"}, make_client=ClientFactory())

    message = str(raised.value)
    assert "VISION_MODEL" in message and name in message
    assert "vu" not in message


def test_blank_vision_model_with_vision_key_is_refused():
    with pytest.raises(ModelSetupError) as raised:
        build_models(task(), {}, {**TEXT_ENV, "VISION_MODEL": "  ", "VISION_API_KEY": "vk"},
                     make_client=ClientFactory())

    assert "VISION_MODEL" in str(raised.value)


def test_blank_vision_values_alone_mean_one_model_mode():
    environ = {**TEXT_ENV, "VISION_MODEL": " ", "VISION_API_KEY": "", "VISION_BASE_URL": "  "}

    assert vision_setup({}, environ, make_client=ClientFactory()) == (None, None)


def test_missing_text_key_is_refused_without_values():
    with pytest.raises(ModelSetupError) as raised:
        build_models(task(), {}, {"OPENAI_BASE_URL": "u", "OPENAI_MODEL": "text-model"}, make_client=ClientFactory())

    message = str(raised.value)
    assert "OPENAI_API_KEY" in message
    assert all(secret not in message.split() for secret in SECRETS)


def test_vision_settings_reach_the_vision_object():
    settings = {"vision_max_side": 1024, "vision_points": "normalized_1000"}

    vision, _ = vision_setup(settings, {**TEXT_ENV, "VISION_MODEL": "eye-model"}, make_client=ClientFactory())
    default, _ = vision_setup({}, {**TEXT_ENV, "VISION_MODEL": "eye-model"}, make_client=ClientFactory())

    assert (vision.max_side, vision.points) == (1024, "normalized_1000")
    assert (default.max_side, default.points) == (vision_module.DEFAULT_MAX_SIDE, "pixel")


def test_vision_reasoning_only_when_set():
    environ = {**TEXT_ENV, "VISION_MODEL": "eye-model"}
    plain = ClientFactory()
    tuned = ClientFactory()
    omitted = ClientFactory()

    vision_setup({}, environ, make_client=plain)[0].reader.ask("rules", "q", "url")
    vision_setup({"vision_reasoning_effort": "low"}, environ, make_client=tuned)[0].reader.ask("rules", "q", "url")
    vision_setup({"vision_reasoning_effort": "omit"}, environ, make_client=omitted)[0].reader.ask("rules", "q", "url")

    assert "reasoning" not in plain.sent[0]
    assert tuned.sent[0]["reasoning"] == {"effort": "low"}
    assert "reasoning" not in omitted.sent[0]


def test_vision_reader_sends_one_user_message_with_a_high_detail_image():
    factory = ClientFactory()
    reader = VisionReader("eye-model", factory(None, None))

    assert reader.ask("rules", "which mark?", "data:image/png;base64,AA") == ("answer", 7)
    sent = factory.sent[0]
    assert sent["model"] == "eye-model"
    assert sent["instructions"] == "rules"
    assert sent["input"] == [{"role": "user", "content": [
        {"type": "input_text", "text": "which mark?"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA", "detail": "high"}]}]
    assert "tools" not in sent and "reasoning" not in sent


def test_vision_reader_survives_a_reply_without_text_or_usage():
    factory = ClientFactory(reply=SimpleNamespace(output_text=None, usage=None))

    assert VisionReader("eye-model", factory(None, None)).ask("rules", "q", "url") == ("", 0)


def test_text_responder_uses_the_task_model_and_effort():
    factory = ClientFactory()

    responder = text_responder(task(reasoning_effort="omit"), TEXT_ENV, make_client=factory)
    responder.respond("rules", [], [])

    assert factory.sent[0]["model"] == "text-model"
    assert "reasoning" not in factory.sent[0]


def test_start_line_names_sources_not_models():
    environ = {**TEXT_ENV, "VISION_MODEL": "eye-model", "VISION_API_KEY": "vk", "VISION_BASE_URL": "vu"}

    split = start_line(build_models(task(), {}, environ, make_client=ClientFactory()))
    single = start_line(build_models(task(model_source="기본값"), {}, TEXT_ENV, make_client=ClientFactory()))

    assert split == "텍스트 모델: env · 비전 모델: env"
    assert single == "텍스트 모델: 기본값 · 비전 모델: 없음"
    for line in (split, single):
        assert all(secret not in line.replace("텍스트 모델", "").replace("비전 모델", "").split() for secret in SECRETS)
        assert "text-model" not in line and "eye-model" not in line


@pytest.mark.parametrize("mode", ["off", "tool_output"])
def test_start_line_notes_ignored_image_mode(mode):
    environ = {**TEXT_ENV, "VISION_MODEL": "eye-model"}

    models = build_models(task(image_mode=mode), {}, environ, make_client=ClientFactory())

    assert models.image_mode_ignored is True
    assert models.vision is not None
    assert start_line(models) == "텍스트 모델: env · 비전 모델: env · image_mode 무시(나눈 방식)"


def test_image_mode_is_not_ignored_without_vision_or_with_the_default():
    one_model = build_models(task(image_mode="off"), {}, TEXT_ENV, make_client=ClientFactory())
    split_default = build_models(task(), {}, {**TEXT_ENV, "VISION_MODEL": "eye-model"}, make_client=ClientFactory())

    assert one_model.image_mode_ignored is False and "image_mode" not in start_line(one_model)
    assert split_default.image_mode_ignored is False


def test_point_modes_match_settings():
    assert vision_module.POINT_MODES == settings_module.VISION_POINT_MODES


def test_omit_sends_no_reasoning():
    client, sent = recording_client()

    assert OpenAIResponder("m", "omit", client=client).respond("rules", [], []) == "response"
    assert "reasoning" not in sent[0]
    assert sent[0]["model"] == "m"
    assert sent[0]["parallel_tool_calls"] is False


def test_effort_is_sent_as_before():
    client, sent = recording_client()

    OpenAIResponder("m", "low", client=client).respond("rules", [], [])

    assert sent[0]["reasoning"] == {"effort": "low"}


def test_omit_for_one_call():
    client, sent = recording_client()
    responder = OpenAIResponder("m", "low", client=client)

    responder.respond("rules", [], [], effort="omit")
    responder.respond("rules", [], [])

    assert "reasoning" not in sent[0]
    assert sent[1]["reasoning"] == {"effort": "low"}


def test_a_call_effort_is_sent_when_the_responder_omits():
    client, sent = recording_client()
    responder = OpenAIResponder("m", "omit", client=client)

    responder.respond("rules", [], [], effort="medium")
    responder.respond("rules", [], [])

    assert sent[0]["reasoning"] == {"effort": "medium"}
    assert "reasoning" not in sent[1]
