"""모델 어댑터 (spec 19장). 역할(extract, summarize ...)을 요청하면 설정 순서대로 모델을 고른다.

CLI 어댑터는 사용자가 이 PC에서 로그인해 둔 공식 CLI(Claude Code, Codex CLI, Gemini CLI)를 비대화형으로 실행한다.
Orbit은 키나 세션 토큰을 저장하지 않는다. 실행 규칙은 spec 19.2를 따른다.

- 매번 새로 만든 빈 임시 폴더에서 실행한다. 볼트·프로젝트 파일이 컨텍스트로 끌려오지 않게 하기 위해서다
- 도구는 끈다. 이미지 첨부가 있을 때만 파일 읽기를 허용한다
- 출력은 JSON 스키마로 받고, 어댑터가 한 번 더 파싱한다
- 동시에 한 건씩 실행한다
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .extract import ChatModel, OllamaModel

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 120
_cli_lock = threading.Lock()


class ModelError(RuntimeError):
    pass


class LimitReached(ModelError):
    pass


def strict_schema(schema: dict) -> dict:
    """OpenAI strict 스키마로 바꾼다. 모든 속성 required, additionalProperties false, null 허용 타입은 유지한다.

    codex `--output-schema`가 이 형식을 요구하고, 다른 CLI에도 해가 없어 공통으로 쓴다.
    """
    s = copy.deepcopy(schema)

    def walk(node: dict) -> None:
        t = node.get("type")
        if t == "object" or (isinstance(t, list) and "object" in t):
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
            for v in node.get("properties", {}).values():
                walk(v)
        if "items" in node:
            walk(node["items"])
        # strict 모드는 minimum/maximum을 지원하지 않는 제공사가 있어 뺀다. 범위는 파싱 후 잘라낸다
        node.pop("minimum", None)
        node.pop("maximum", None)

    walk(s)
    return s


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        raise ModelError(f"JSON 없음: {text[:300]}")
    return json.loads(text[a : b + 1])


def render_messages(messages: list[dict]) -> str:
    """chat 메시지 목록을 CLI 프롬프트 한 덩어리로 편다. few-shot 예시는 입력·출력 쌍으로 남긴다."""
    parts = []
    for m in messages:
        if m["role"] == "system":
            parts.append(m["content"])
        elif m["role"] == "user":
            parts.append("입력:\n" + m["content"])
        else:
            parts.append("출력:\n" + m["content"])
    parts.append("출력:")
    return "\n\n".join(parts)


def _err_text(stdout: str, stderr: str) -> str:
    # claude -p --output-format json은 실패해도 stdout JSON의 result에 사유를 넣는다(예: Not logged in)
    try:
        env = json.loads(stdout)
        if isinstance(env, dict) and env.get("result"):
            return str(env["result"])[:1500]
    except json.JSONDecodeError:
        pass
    return (stderr or stdout)[-1500:]


_LIMIT_RE = re.compile(r"usage limit|rate limit|quota|limit reached|429|한도", re.IGNORECASE)


class CliModel:
    """CLI 구독 어댑터의 공통부. `ChatModel` 규약(`chat_json`)을 따른다."""

    kind = ""
    default_command = ""

    def __init__(self, command: str = "", model: str = "", timeout: float = DEFAULT_TIMEOUT_S,
                 extra_args: list[str] | None = None):
        self.command = command or self._find_command()
        self.model = model
        self.timeout = timeout
        self.extra_args = list(extra_args or [])
        self.name = f"{self.kind}:{model}" if model else self.kind

    def _find_command(self) -> str:
        return self.default_command

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def chat_json(self, messages: list[dict], schema: dict, images: list[Path] | None = None) -> dict:
        return self.run(render_messages(messages), schema, images)

    def run(self, prompt: str, schema: dict, images: list[Path] | None = None) -> dict:
        images = images or []
        schema = strict_schema(schema)
        with _cli_lock, tempfile.TemporaryDirectory(prefix="orbit-cli-") as tmp:
            wd = Path(tmp)
            names = []
            for i, img in enumerate(images):
                name = f"attach{i}{img.suffix.lower()}"
                shutil.copy2(img, wd / name)
                names.append(name)
            argv = self.argv(wd, schema, names)
            full = prompt + self.prompt_suffix(names)
            try:
                p = subprocess.run(argv, input=full, capture_output=True, text=True, cwd=wd,
                                   timeout=self.timeout, check=False)
            except FileNotFoundError as e:
                raise ModelError(f"{self.command} 실행 파일 없음") from e
            except subprocess.TimeoutExpired as e:
                raise ModelError(f"{self.kind} 시간 초과 {self.timeout}s") from e
            if p.returncode != 0:
                msg = _err_text(p.stdout, p.stderr)
                if _LIMIT_RE.search(msg):
                    raise LimitReached(f"{self.kind} 한도 초과: {msg}")
                raise ModelError(f"{self.kind} 종료코드 {p.returncode}: {msg}")
            return self.parse(p.stdout, wd)

    def argv(self, workdir: Path, schema: dict, attachments: list[str]) -> list[str]:
        raise NotImplementedError

    def parse(self, stdout: str, workdir: Path) -> dict:
        return extract_json(stdout)

    def prompt_suffix(self, attachments: list[str]) -> str:
        return "\n\n첨부 파일(현재 폴더): " + ", ".join(attachments) if attachments else ""


class ClaudeCli(CliModel):
    kind = "claude"
    default_command = "claude"

    def _find_command(self) -> str:
        # Ubuntu snap 설치는 실행 파일명이 claude-code다
        if not shutil.which("claude") and shutil.which("claude-code"):
            return "claude-code"
        return "claude"

    def argv(self, workdir, schema, attachments):
        a = [self.command, "-p", "--output-format", "json",
             "--json-schema", json.dumps(schema, ensure_ascii=False),
             "--tools", "Read" if attachments else "",
             "--no-session-persistence", "--strict-mcp-config"]
        if self.model:
            a += ["--model", self.model]
        return a + self.extra_args

    def parse(self, stdout, workdir):
        env = json.loads(stdout)
        if env.get("is_error"):
            raise ModelError(f"claude 오류: {str(env.get('result'))[:1500]}")
        so = env.get("structured_output")
        if isinstance(so, dict):
            return so
        return extract_json(str(env.get("result", "")))


class CodexCli(CliModel):
    kind = "codex"
    default_command = "codex"

    def argv(self, workdir, schema, attachments):
        (workdir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        a = [self.command, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
             "--color", "never", "--output-schema", str(workdir / "schema.json"),
             "--output-last-message", str(workdir / "last.txt")]
        for n in attachments:
            a += ["--image", str(workdir / n)]
        if self.model:
            a += ["--model", self.model]
        return a + self.extra_args + ["-"]

    def parse(self, stdout, workdir):
        last = workdir / "last.txt"
        return extract_json(last.read_text(encoding="utf-8") if last.exists() else stdout)

    def prompt_suffix(self, attachments):
        return ""


class GeminiCli(CliModel):
    kind = "gemini"
    default_command = "gemini"

    def argv(self, workdir, schema, attachments):
        # gemini -p는 stdin 내용 뒤에 -p 문자열을 붙인다. 본문은 stdin, -p에는 출력 형식만 둔다
        hint = "위 지시에 따라 다음 JSON 스키마에 맞는 JSON 객체 하나만 출력: " + json.dumps(schema, ensure_ascii=False)
        if attachments:
            hint += "\n첨부: " + " ".join(f"@{n}" for n in attachments)
        a = [self.command, "-p", hint, "--output-format", "json"]
        if self.model:
            a += ["--model", self.model]
        return a + self.extra_args

    def parse(self, stdout, workdir):
        try:
            env = json.loads(stdout)
        except json.JSONDecodeError:
            return extract_json(stdout)
        if isinstance(env, dict) and "response" in env:
            if env.get("error"):
                raise ModelError(f"gemini 오류: {env['error']}")
            return extract_json(str(env["response"]))
        return env

    def prompt_suffix(self, attachments):
        return ""


CLI_KINDS = {c.kind: c for c in (ClaudeCli, CodexCli, GeminiCli)}


# ── 역할 라우팅 (spec 19.3, 19.4) ─────────────────────────────────────────


@dataclass
class Provider:
    name: str
    model: Any  # ChatModel
    trust: str  # local | remote
    daily_calls: int | None = None


@dataclass
class Router:
    providers: dict[str, Provider]
    roles: dict[str, list[str]]
    conn: sqlite3.Connection | None = None
    _blocked: set[str] = field(default_factory=set)

    def candidates(self, role: str, allow_remote: bool = True) -> list[Provider]:
        names = self.roles.get(role) or []
        out = []
        for n in names:
            p = self.providers.get(n)
            if p is None or n in self._blocked:
                continue
            if p.trust == "remote" and not allow_remote:
                continue
            out.append(p)
        return out

    def chat_json(self, role: str, messages: list[dict], schema: dict, *, images: list[Path] | None = None,
                  allow_remote: bool = True) -> tuple[dict, str]:
        """앞 후보부터 시도하고 실패·한도 초과면 다음으로 넘어간다. (결과, 쓴 모델 이름)."""
        errors = []
        for p in self.candidates(role, allow_remote):
            if not self._take_quota(p):
                errors.append(f"{p.name}: 일일 상한 {p.daily_calls}회 도달")
                continue
            try:
                if images:
                    if not isinstance(p.model, CliModel):
                        errors.append(f"{p.name}: 첨부 미지원")
                        continue
                    return p.model.chat_json(messages, schema, images=images), p.model.name
                return p.model.chat_json(messages, schema), p.model.name
            except LimitReached as e:
                # 이번 프로세스 동안 후보에서 뺀다. 재시작하면 다시 시도한다
                self._blocked.add(p.name)
                errors.append(str(e))
            except Exception as e:  # 모델 하나의 실패로 역할 전체를 실패시키지 않는다
                errors.append(f"{p.name}: {e}")
        raise ModelError(f"역할 {role}: 쓸 수 있는 모델 없음 ({'; '.join(errors) or '후보 없음'})")

    def _take_quota(self, p: Provider) -> bool:
        if not p.daily_calls or self.conn is None:
            return True
        today = date.today().isoformat()
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_usage (provider TEXT, day TEXT, calls INTEGER, PRIMARY KEY (provider, day))"
        )
        row = self.conn.execute("SELECT calls FROM llm_usage WHERE provider=? AND day=?", (p.name, today)).fetchone()
        used = row[0] if row else 0
        if used >= p.daily_calls:
            return False
        self.conn.execute(
            "INSERT INTO llm_usage VALUES (?, ?, 1) ON CONFLICT(provider, day) DO UPDATE SET calls = calls + 1",
            (p.name, today),
        )
        return True


DEFAULT_LLM_CONFIG: dict = {
    # CLI 구독을 기본으로 둔다(2026-11-03 사용자 결정). 로컬 모델은 providers에 추가하고 roles 앞에 넣으면 된다
    "providers": {"claude": {"kind": "cli", "command": "claude"}},
    "roles": {"extract": ["claude"], "summarize": ["claude"]},
    "limits": {"claude": {"daily_calls": 50}},
}


def build_router(cfg: dict | None, conn: sqlite3.Connection | None = None) -> Router:
    cfg = cfg or DEFAULT_LLM_CONFIG
    limits = cfg.get("limits") or {}
    providers = {}
    for name, p in (cfg.get("providers") or {}).items():
        kind = p.get("kind")
        if kind == "cli":
            command = p.get("command", name)
            cls = next((c for k, c in CLI_KINDS.items() if Path(command).name.startswith(k)), None)
            if cls is None:
                raise ValueError(f"알 수 없는 CLI: {command} (claude | codex | gemini)")
            # claude 기본값이면 snap 설치(claude-code)도 찾도록 빈 값으로 넘긴다
            model = cls(command="" if command == cls.default_command else command,
                        model=p.get("model", ""), timeout=p.get("timeout_s", DEFAULT_TIMEOUT_S),
                        extra_args=p.get("args"))
            trust = "remote"
        elif kind == "ollama":
            model = OllamaModel(p.get("url", "http://localhost:11434"), p["model"])
            trust = "local"
        else:
            raise ValueError(f"providers.{name}.kind 지원 안 함: {kind}")
        providers[name] = Provider(name, model, trust, (limits.get(name) or {}).get("daily_calls"))
    return Router(providers, cfg.get("roles") or {}, conn)


def as_chat_model(router: Router, role: str) -> ChatModel:
    """기존 `extract()`처럼 ChatModel 하나를 받는 코드에 라우터를 끼우는 얇은 감싸개."""

    class _Routed:
        name = f"role:{role}"

        def chat_json(self, messages, schema):
            out, used = router.chat_json(role, messages, schema)
            self.name = used
            return out

    return _Routed()
