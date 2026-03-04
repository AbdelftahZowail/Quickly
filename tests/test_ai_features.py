import pytest
from sqlalchemy import select

from app.models import AppSetting
import app.ai_classifier as ai_classifier


def _add_setting(session, key, value):
    session.add(AppSetting(key=key, value=value))


@pytest.mark.asyncio
async def test_temperature_flag_and_retry(session, monkeypatch):
    # configure reply_classifier feature
    provider = "testprov"
    model = "badmodel"
    api_key = "shrug"
    _add_setting(session, "ai_reply_classifier_enabled", "true")
    _add_setting(session, "ai_reply_classifier_provider", provider)
    _add_setting(session, "ai_reply_classifier_model", model)
    _add_setting(session, "ai_reply_classifier_api_key", api_key)
    await session.commit()

    called_kwargs = []

    async def fake_completion(*args, **kwargs):
        # record everything
        called_kwargs.append(kwargs.copy())
        if len(called_kwargs) == 1:
            # simulate provider error about unsupported temperature
            raise Exception("Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported.")
        # success on retry
        class ReplyMsg:
            def __init__(self):
                self.content = "interested"

        class Choice:
            def __init__(self):
                self.message = ReplyMsg()

        return type("Resp", (), {"choices": [Choice()]})()

    monkeypatch.setattr(ai_classifier, "acompletion", fake_completion)

    # first invocation should trigger error and retry
    result = await ai_classifier.classify_reply(session, "hi")
    assert result == "interested"

    # flag should be stored in settings
    key = f"ai_model_{provider}_{model}_no_temperature"
    res = await session.execute(select(AppSetting).where(AppSetting.key == key))
    assert res.scalars().first() is not None

    # second invocation should omit temperature
    result2 = await ai_classifier.classify_reply(session, "again")
    assert result2 == "interested"
    assert len(called_kwargs) >= 2
    assert "temperature" not in called_kwargs[1]
