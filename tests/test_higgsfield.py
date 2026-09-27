import pytest

from cloudos import higgsfield
from cloudos.config import reset_settings_cache
from cloudos.contracts import CloudOSError, ErrorCode
from cloudos.router.providers import base


def test_generation_requires_confirmation(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_ALLOW_GENERATION", "true")
    reset_settings_cache()
    with pytest.raises(CloudOSError) as error:
        higgsfield.generate("image", "shoe ad", confirmed=False)
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    reset_settings_cache()


def test_generation_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_ALLOW_GENERATION", "false")
    reset_settings_cache()
    with pytest.raises(CloudOSError) as error:
        higgsfield.generate("video", "shoe ad", confirmed=True)
    assert error.value.code is ErrorCode.PAID_DISABLED
    reset_settings_cache()


def test_generation_uses_fixed_argv_without_shell(monkeypatch):
    calls = []
    monkeypatch.setenv("HIGGSFIELD_ALLOW_GENERATION", "true")
    reset_settings_cache()
    monkeypatch.setattr(higgsfield.shutil, "which", lambda binary: "/bin/higgsfield")

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return base.CliResult(0, '{"result_url":"https://example.test/result"}', "")

    monkeypatch.setattr(base, "RUNNER", runner)
    prompt = 'product shot; echo "not a shell"'
    output = higgsfield.generate("image", prompt, confirmed=True)

    argv, kwargs = calls[0]
    assert argv[:4] == ["/bin/higgsfield", "generate", "create", "gpt_image_2_5"]
    assert argv[argv.index("--prompt") + 1] == prompt
    assert "shell" not in kwargs
    assert "result_url" in output
    reset_settings_cache()
