import base64
import threading
import time
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aider.commands import Commands
from aider.io import InputOutput
from aider.models import Model
from aider.providers import get_provider
from aider.utils import is_image_file


def clipboard_commands(monkeypatch, tmp_path, vision=True):
    image = Image.new("RGB", (32, 32), "red")
    monkeypatch.setattr("aider.commands.ImageGrab.grabclipboard", lambda: image)
    monkeypatch.setattr("aider.commands.tempfile.mkdtemp", lambda: str(tmp_path))
    io = SimpleNamespace(tool_output=MagicMock(), tool_error=MagicMock(), placeholder=None)
    coder = SimpleNamespace(main_model=SimpleNamespace(info={"supports_vision": vision}),
                            abs_fnames=set(), check_added_files=MagicMock())
    return Commands(io, coder)


def test_images_prepare_draft_and_do_not_replace_previous(monkeypatch, tmp_path):
    commands = clipboard_commands(monkeypatch, tmp_path)
    assert commands.cmd_paste("") is None
    assert commands.io.placeholder == "[image #1] "
    assert commands.paste_clipboard() == "[image #2] "
    assert len(commands.coder.abs_fnames) == 2
    assert all(Image.open(name).format == "PNG" for name in commands.coder.abs_fnames)


def test_disabled_vision_does_not_attach(monkeypatch, tmp_path):
    commands = clipboard_commands(monkeypatch, tmp_path, vision=False)
    assert commands.paste_clipboard() is None
    assert not commands.coder.abs_fnames
    commands.io.tool_error.assert_called_once()


def test_clipboard_text_and_failure_keep_draft(monkeypatch, tmp_path):
    commands = clipboard_commands(monkeypatch, tmp_path)
    monkeypatch.setattr("aider.commands.ImageGrab.grabclipboard", lambda: None)
    monkeypatch.setattr("aider.commands.pyperclip.paste", lambda: "plain text")
    commands.cmd_paste("")
    assert commands.io.placeholder == "plain text"
    monkeypatch.setattr("aider.commands.ImageGrab.grabclipboard", MagicMock(side_effect=OSError("failed")))
    commands.cmd_paste("")
    assert commands.io.placeholder == "plain text"
    assert not commands._reading_clipboard


@pytest.mark.parametrize("sequence,expected,reads", [
    ("\x16", "[image #1] ", 1),
    ("\x1b[200~\x1b[201~", "[image #1] ", 1),
    ("\x1b[200~ordinary text\x1b[201~", "ordinary text", 0),
])
def test_terminal_paste_bindings(monkeypatch, tmp_path, sequence, expected, reads):
    monkeypatch.setattr("aider.io.is_dumb_terminal", lambda: False)
    commands = SimpleNamespace(get_commands=lambda: [], paste_clipboard=MagicMock(return_value="[image #1] "))
    with create_pipe_input() as pipe:
        io = InputOutput(input=pipe, output=DummyOutput(), pretty=False)

        def type_and_submit():
            pipe.send_text(sequence)
            deadline = time.monotonic() + 3
            while io.prompt_session.default_buffer.text != expected and time.monotonic() < deadline:
                time.sleep(0.01)
            pipe.send_text("\r")

        thread = threading.Thread(target=type_and_submit, daemon=True)
        thread.start()
        assert io.get_input(str(tmp_path), [], [], commands) == expected
        thread.join(timeout=1)
        assert commands.paste_clipboard.call_count == reads


@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses", "anthropic"])
def test_image_request_payload(protocol):
    picture = BytesIO()
    Image.new("RGB", (32, 32), "red").save(picture, format="PNG")
    data = base64.b64encode(picture.getvalue()).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图片"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}},
    ]}]
    provider = get_provider("test-model", protocol=protocol, api_key="test-key")
    client = MagicMock()
    provider._client = client
    provider.create_completion(messages, stream=True)
    if protocol == "anthropic":
        block = client.messages.create.call_args.kwargs["messages"][0]["content"][1]
        assert block["type"] == "image" and block["source"]["data"] == data
    elif protocol == "openai-responses":
        block = client.responses.create.call_args.kwargs["input"][0]["content"][1]
        assert block["type"] == "input_image" and block["image_url"].endswith(data)
    else:
        assert client.chat.completions.create.call_args.kwargs["messages"] == messages


def test_image_bytes_are_not_counted_as_text():
    model = Model("gpt-4", weak_model=False, editor_model=False)
    picture = BytesIO()
    Image.effect_noise((768, 768), 100).save(picture, format="PNG")
    data = base64.b64encode(picture.getvalue()).decode()
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + data,
    }}]}]
    assert model.token_count(messages) < 5000
    assert messages[0]["content"][0]["image_url"]["url"].endswith(data)
    assert is_image_file("screenshot.PNG")
