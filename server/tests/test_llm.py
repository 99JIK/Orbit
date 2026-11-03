import json
import sys
from pathlib import Path

import pytest

from orbit_server import llm

# 가짜 CLI를 shebang 스크립트로 만든다. Windows는 shebang을 실행하지 않는다
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="shebang 스크립트")


def fake_cli(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def test_strict_schema_requires_all_and_drops_ranges():
    s = llm.strict_schema({
        "type": "object",
        "properties": {"a": {"type": "number", "minimum": 0}, "b": {"type": "array", "items": {
            "type": "object", "properties": {"c": {"type": ["string", "null"]}}}}},
        "required": ["a"],
    })
    assert s["required"] == ["a", "b"] and s["additionalProperties"] is False
    assert "minimum" not in s["properties"]["a"]
    inner = s["properties"]["b"]["items"]
    assert inner["required"] == ["c"] and inner["additionalProperties"] is False


@pytest.mark.parametrize("text", ['{"a": 1}', 'ok\n```json\n{"a": 1}\n```', 'x {"a": 1} y'])
def test_extract_json(text):
    assert llm.extract_json(text) == {"a": 1}


def test_render_messages_keeps_few_shot_pairs():
    out = llm.render_messages([
        {"role": "system", "content": "규칙"}, {"role": "user", "content": "예시"},
        {"role": "assistant", "content": "{}"}, {"role": "user", "content": "<data>x</data>"},
    ])
    assert out.startswith("규칙") and "입력:\n예시\n\n출력:\n{}" in out and out.endswith("출력:")


@posix_only
def test_claude_structured_output_and_tools_off(tmp_path):
    cmd = fake_cli(tmp_path, "claude", r"""
import sys, json, os
a = sys.argv[1:]
assert "-p" in a and a[a.index("--tools") + 1] == ""
schema = json.loads(a[a.index("--json-schema") + 1])
assert schema["additionalProperties"] is False
assert os.listdir(".") == []  # 빈 임시 폴더에서 실행
print(json.dumps({"is_error": False, "result": "", "structured_output": {"got": sys.stdin.read()[:2]}}))
""")
    assert llm.ClaudeCli(command=cmd).run("안녕하세요", {"type": "object", "properties": {}}) == {"got": "안녕"}


@posix_only
def test_claude_attachment_enables_read_only(tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"png")
    cmd = fake_cli(tmp_path, "claude", r"""
import sys, json, os
a = sys.argv[1:]
assert a[a.index("--tools") + 1] == "Read" and os.path.exists("attach0.png")
print(json.dumps({"result": "{\"ok\": 1}"}))
""")
    assert llm.ClaudeCli(command=cmd).run("p", {"type": "object", "properties": {}}, [img]) == {"ok": 1}


@posix_only
def test_codex_reads_last_message(tmp_path):
    cmd = fake_cli(tmp_path, "codex", r"""
import sys, json
a = sys.argv[1:]
assert a[0] == "exec" and a[-1] == "-" and "read-only" in a
json.load(open(a[a.index("--output-schema") + 1]))
open(a[a.index("--output-last-message") + 1], "w").write('{"ok": 2}')
print("log noise")
""")
    assert llm.CodexCli(command=cmd).run("p", {"type": "object", "properties": {}}) == {"ok": 2}


@posix_only
def test_gemini_response_envelope(tmp_path):
    cmd = fake_cli(tmp_path, "gemini", r"""
import sys, json
a = sys.argv[1:]
assert a[0] == "-p" and "JSON" in a[1]
sys.stdin.read()
print(json.dumps({"response": "```json\n{\"ok\": 3}\n```"}))
""")
    assert llm.GeminiCli(command=cmd).run("p", {"type": "object", "properties": {}}) == {"ok": 3}


@posix_only
def test_not_logged_in_reason_is_reported(tmp_path):
    cmd = fake_cli(tmp_path, "claude", r"""
import json, sys
print(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))
sys.exit(1)
""")
    with pytest.raises(llm.ModelError, match="Not logged in"):
        llm.ClaudeCli(command=cmd).run("p", {"type": "object", "properties": {}})


@posix_only
def test_limit_message_raises_limit_reached(tmp_path):
    cmd = fake_cli(tmp_path, "claude", "import sys; print('Claude usage limit reached', file=sys.stderr); sys.exit(1)")
    with pytest.raises(llm.LimitReached):
        llm.ClaudeCli(command=cmd).run("p", {"type": "object", "properties": {}})


class Fake:
    def __init__(self, name, out=None, exc=None):
        self.name, self.out, self.exc, self.calls = name, out, exc, 0

    def chat_json(self, messages, schema):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.out


def test_router_falls_back_and_blocks_limited():
    a = Fake("a", exc=llm.LimitReached("한도"))
    b = Fake("b", out={"ok": True})
    r = llm.Router({"a": llm.Provider("a", a, "remote"), "b": llm.Provider("b", b, "local")},
                   {"summarize": ["a", "b"]})
    assert r.chat_json("summarize", [], {}) == ({"ok": True}, "b")
    r.chat_json("summarize", [], {})
    assert a.calls == 1  # 한도 초과한 후보는 다시 시도하지 않는다


def test_router_respects_remote_flag_and_daily_limit(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    remote = Fake("r", out={"r": 1})
    r = llm.Router({"r": llm.Provider("r", remote, "remote", daily_calls=1)}, {"chat": ["r"]}, conn)
    with pytest.raises(llm.ModelError, match="후보 없음"):
        r.chat_json("chat", [], {}, allow_remote=False)
    r.chat_json("chat", [], {})
    with pytest.raises(llm.ModelError, match="일일 상한"):
        r.chat_json("chat", [], {})


def test_build_router_from_config():
    r = llm.build_router({
        "providers": {"local": {"kind": "ollama", "model": "gemma3:4b"},
                      "codex": {"kind": "cli", "command": "codex", "model": "gpt-5"}},
        "roles": {"summarize": ["codex", "local"]},
        "limits": {"codex": {"daily_calls": 10}},
    })
    assert [p.name for p in r.candidates("summarize")] == ["codex", "local"]
    assert isinstance(r.providers["codex"].model, llm.CodexCli) and r.providers["codex"].daily_calls == 10
    assert r.providers["local"].trust == "local"
    with pytest.raises(ValueError):
        llm.build_router({"providers": {"x": {"kind": "cli", "command": "gpt"}}})


def test_default_config_uses_cli_subscription():
    r = llm.build_router(None)
    assert isinstance(r.providers["claude"].model, llm.ClaudeCli)
    assert json.dumps(llm.DEFAULT_LLM_CONFIG)  # yaml로 옮겨 적을 수 있는 순수 값
