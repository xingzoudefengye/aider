"""Aider 本地 Web 管理服务；配置和会话仅来自当前 Aider 项目。"""

import argparse
import json
import os
import secrets
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from aider.model_options import effective_model_options

CONFIG_FILENAME = ".aider.providers.json"
HISTORY_FILENAME = ".aider.chat.history.md"
SESSION_META_FILENAME = ".aider.sessions.json"
SUPPORTED_PROTOCOLS = frozenset({"openai-chat", "anthropic", "openai-responses"})
HTML = Path(__file__).with_name("web.html").read_text(encoding="utf-8")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _responses_test_result(text):
    """兼容普通 JSON 和网关强制返回的 Responses SSE。"""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(str(data["error"]))
        return bool(data.get("id") and
                    (isinstance(data.get("output"), list) or isinstance(data.get("output_text"), str)))
    valid = False
    for line in text.splitlines():
        if not line.strip().startswith("data:"):
            continue
        try:
            event = json.loads(line.strip()[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        response = event.get("response")
        response = response if isinstance(response, dict) else {}
        if event.get("type") in ("error", "response.error", "response.failed") or response.get("error"):
            raise ValueError(str(event.get("error") or response.get("error") or "Responses 流式请求失败"))
        if event.get("type") == "response.completed":
            valid = True
        elif event.get("type") == "response.created" and response.get("id"):
            valid = True
    return valid


def _safe_provider(provider):
    result = {k: v for k, v in provider.items() if k != "api_key"}
    result.update(effective_model_options(provider))
    result["has_api_key_env"] = bool(provider.get("api_key_env"))
    result["has_api_key"] = bool(provider.get("api_key") or
                                 os.environ.get(provider.get("api_key_env") or ""))
    return result


class WebStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config_path = self.root / CONFIG_FILENAME
        self.history_path = self.root / HISTORY_FILENAME
        self.session_meta_path = self.root / SESSION_META_FILENAME
        self.lock = threading.RLock()

    def _read_json(self, path, default):
        if not path.exists():
            return default
        # 已有文件损坏时报告错误，避免保存操作覆盖原配置。
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _read_config(self):
        data = self._read_json(self.config_path, {"providers": []})
        return data if isinstance(data, list) else data.get("providers", [])

    def _write_config(self, providers):
        data = self._read_json(self.config_path, {})
        if not isinstance(data, dict):
            data = {}
        data["providers"] = providers
        self._write_json(self.config_path, data)

    def list_providers(self):
        with self.lock:
            return [_safe_provider(p) for p in self._read_config()]

    def get_provider(self, provider_id):
        with self.lock:
            return next((p for p in self._read_config() if p.get("id") == provider_id), None)

    def save_provider(self, provider, provider_id=None):
        if not isinstance(provider, dict):
            raise ValueError("Provider 必须是 JSON 对象")
        with self.lock:
            providers = self._read_config()
            existing = next((p for p in providers if p.get("id") == provider_id), None)
            if provider_id and existing is None:
                raise KeyError("模型不存在")
            value = dict(existing or {})
            allowed = ("name", "protocol", "model", "api_base", "api_key", "api_key_env",
                       "enabled", "is_default", "context_window", "vision", "reasoning_effort")
            value.update({k: provider[k] for k in allowed if k in provider})
            # 可选设置留空表示恢复默认，编辑供应商时不覆盖模型各自的设置。
            if "context_window" in provider:
                context = provider["context_window"]
                if context in (None, ""):
                    value.pop("context_window", None)
                else:
                    if isinstance(context, bool) or not str(context).isdigit() or int(context) < 1:
                        raise ValueError("上下文窗口必须是正整数")
                    value["context_window"] = int(context)
            if "vision" in provider:
                vision = provider["vision"]
                if vision in (None, ""):
                    value.pop("vision", None)
                elif isinstance(vision, bool) or vision in ("true", "false"):
                    value["vision"] = _as_bool(vision)
                else:
                    raise ValueError("图片输入必须为支持、不支持或默认")
            if "reasoning_effort" in provider:
                effort = provider["reasoning_effort"]
                if effort in (None, ""):
                    value.pop("reasoning_effort", None)
                elif effort in ("low", "medium", "high", "xhigh", "max"):
                    value["reasoning_effort"] = effort
                else:
                    raise ValueError("不支持的思考强度")
            if existing and not provider.get("api_key"):
                value["api_key"] = existing.get("api_key")
            source_id = provider.get("source_provider_id")
            if source_id and not existing:
                source = next((p for p in providers if p.get("id") == source_id), None)
                if source is None:
                    raise ValueError("源供应商不存在")
                # URL 和密钥继承供应商，协议由新模型自行选择。
                for key in ("name", "api_base", "api_key", "api_key_env"):
                    value[key] = source.get(key)
                value.setdefault("protocol", source.get("protocol"))
            value["protocol"] = value.get("protocol") or "openai-chat"
            if value["protocol"] not in SUPPORTED_PROTOCOLS:
                raise ValueError("不支持的 API 协议")
            value["model"] = str(value.get("model") or "").strip()
            if not value["model"]:
                raise ValueError("模型 ID 不能为空")
            base = str(value.get("api_base") or "").strip()
            if base and (urlparse(base).scheme not in ("http", "https") or not urlparse(base).hostname):
                raise ValueError("Base URL 必须是有效的 http 或 https 地址")
            value["api_base"] = base
            value["name"] = str(value.get("name") or urlparse(base).hostname or "供应商").strip()
            value["enabled"] = _as_bool(value.get("enabled"), True)
            value["is_default"] = _as_bool(value.get("is_default"))
            value.update(effective_model_options(value))
            value["id"] = provider_id or uuid.uuid4().hex
            if value["is_default"]:
                for item in providers:
                    item["is_default"] = False
            if existing is not None:
                providers[providers.index(existing)] = value
            else:
                providers.append(value)
            self._write_config(providers)
            return _safe_provider(value)

    def delete_provider(self, provider_id):
        with self.lock:
            providers = self._read_config()
            remaining = [p for p in providers if p.get("id") != provider_id]
            if len(remaining) == len(providers):
                return False
            self._write_config(remaining)
            return True

    def test_provider(self, provider_id):
        provider = self.get_provider(provider_id)
        if provider is None:
            return {"ok": False, "error": "模型不存在"}
        key = provider.get("api_key") or os.environ.get(provider.get("api_key_env") or "")
        protocol = provider.get("protocol", "openai-chat")
        base = (provider.get("api_base") or
                ("https://api.anthropic.com" if protocol == "anthropic" else
                 "https://api.openai.com/v1")).rstrip("/")
        endpoint = {"anthropic": "/messages", "openai-chat": "/chat/completions",
                    "openai-responses": "/responses"}[protocol]
        if protocol == "anthropic" and not base.endswith(("/v1", "/messages")):
            endpoint = "/v1/messages"
        url = base if base.endswith(endpoint) else base + endpoint
        headers = {"Content-Type": "application/json"}
        payload = {"model": provider["model"]}
        if protocol == "anthropic":
            headers.update({"x-api-key": key or "", "anthropic-version": "2023-06-01"})
            payload.update(messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=64)
        elif protocol == "openai-responses":
            headers["Authorization"] = "Bearer " + (key or "")
            # 参考 ComeCode：部分 Codex 网关要求消息列表、input_text 和流式请求。
            payload.update(input=[{"role": "user", "content": [
                {"type": "input_text", "text": "Reply with OK."}]}],
                stream=True, max_output_tokens=64)
        else:
            headers["Authorization"] = "Bearer " + (key or "")
            payload.update(messages=[{"role": "user", "content": "Reply with OK."}], max_tokens=64)
        started = time.monotonic()
        try:
            with urlopen(Request(url, data=_json_bytes(payload), headers=headers), timeout=30) as response:
                text = response.read().decode("utf-8", "replace")
            if protocol == "openai-responses":
                if not _responses_test_result(text):
                    raise ValueError("服务未返回有效的 Responses 响应，请检查协议和接口地址")
                data = True
            else:
                data = json.loads(text)
                if data.get("error"):
                    raise ValueError(str(data["error"]))
            return {"ok": True, "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "protocol": protocol, "model": provider["model"], "has_response": bool(data)}
        except Exception as error:
            detail = error.read().decode("utf-8", "replace") if isinstance(error, HTTPError) else str(error)
            if key:
                detail = detail.replace(key, "[已隐藏]")
            return {"ok": False, "error": detail[:500],
                    "elapsed_ms": round((time.monotonic() - started) * 1000)}

    def _read_session_meta(self):
        return self._read_json(self.session_meta_path, {})

    def _generate_session_title(self, content):
        for line in content.splitlines():
            if line.startswith("#### ") or line.startswith("#> "):
                title = " ".join(line.split(" ", 1)[1].split())
                if title:
                    return title[:60] + ("…" if len(title) > 60 else "")
        return "无标题会话"

    def _session_messages(self, content):
        """按 Aider 历史的用户标记分段，代码块中的标题不作为消息边界。"""
        messages = []
        role, lines, fence = None, [], None

        def flush():
            text = "\n".join(lines).strip()
            if text and role:
                messages.append({"role": role, "content": text})

        for line in content.splitlines():
            if line.startswith("# aider chat started at "):
                continue
            user = fence is None and (line.startswith("#### ") or line.startswith("#> "))
            if user:
                if role != "user":
                    flush()
                    lines = []
                role = "user"
                lines.append(line.split(" ", 1)[1].rstrip())
            else:
                if role == "user" and line.strip():
                    flush()
                    lines = []
                    role = "assistant"
                elif role is None and line.strip():
                    role = "system"
                lines.append(line.rstrip())
                marker = line.lstrip()[:3]
                if role != "user" and marker in ("```", "~~~"):
                    fence = None if fence == marker else marker if fence is None else fence
        flush()
        return messages

    def _load_sessions(self):
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text(encoding="utf-8", errors="replace").splitlines()
        starts = [i for i, line in enumerate(lines) if line.startswith("# aider chat started at ")]
        metadata = self._read_session_meta()
        modified = datetime.fromtimestamp(self.history_path.stat().st_mtime, timezone.utc).isoformat()
        sessions = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            content = "\n".join(lines[start:end]).strip()
            info = metadata.get(str(index), {})
            # 保留原历史和索引，删除一条不会让其他会话的标题、归档状态错位。
            if info.get("deleted"):
                continue
            updated_at = modified
            continued = self.root / ".aider.session-history" / f"{index}.md"
            if continued.is_file():
                continued_text = continued.read_text(encoding="utf-8", errors="replace").strip()
                # 续聊追加在导出文件中，Web 详情也需读取同一份历史。
                if continued_text.startswith(lines[start]):
                    content = continued_text
                    updated_at = datetime.fromtimestamp(continued.stat().st_mtime, timezone.utc).isoformat()
            sessions.append({"id": str(index), "title": info.get("title") or self._generate_session_title(content),
                             "started_at": lines[start].removeprefix("# aider chat started at "),
                             "updated_at": updated_at, "project": str(self.root),
                             "archived": bool(info.get("archived")), "content": content})
        return list(reversed(sessions))

    def sessions(self, project=None, updated_after=None, updated_before=None, search=None,
                 page=1, page_size=50, archived=None, sort="recent"):
        items = self._load_sessions()
        if project:
            items = [s for s in items if s["project"] == project]
        if updated_after:
            items = [s for s in items if s["updated_at"] >= updated_after]
        if updated_before:
            items = [s for s in items if s["updated_at"] <= updated_before]
        if search:
            items = [s for s in items if search.casefold() in
                     (s["title"] + s["project"] + s["content"]).casefold()]
        if archived is not None:
            items = [s for s in items if s["archived"] == archived]
        if sort in ("project", "title"):
            items.sort(key=lambda s: (s[sort].casefold(), -int(s["id"])))
        if page_size is None:
            return [{k: v for k, v in s.items() if k != "content"} for s in items]
        start = (max(1, int(page)) - 1) * min(200, max(1, int(page_size)))
        return [{k: v for k, v in s.items() if k != "content"}
                for s in items[start:start + min(200, max(1, int(page_size)))]]

    def session_detail(self, session_id):
        session = next((s for s in self._load_sessions() if s["id"] == session_id), None)
        if session:
            session["messages"] = self._session_messages(session["content"])
            history = self.root / ".aider.session-history" / f"{session_id}.md"
            quoted = str(history).replace("'", "''")
            session["restore_command"] = (
                f"aider --restore-chat-history --chat-history-file '{quoted}'"
            )
        return session

    def _update_session(self, session_id, values):
        with self.lock:
            if self.session_detail(session_id) is None:
                raise KeyError("会话不存在")
            metadata = self._read_session_meta()
            metadata.setdefault(session_id, {}).update(values)
            self._write_json(self.session_meta_path, metadata)
            return self.session_detail(session_id)

    def update_session_title(self, session_id, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("标题不能为空")
        return self._update_session(session_id, {"title": title.strip()})

    def archive_session(self, session_id, archived):
        return self._update_session(session_id, {"archived": _as_bool(archived)})

    def delete_session(self, session_id):
        self._update_session(session_id, {"deleted": True})
        return {"ok": True}

    def prepare_session_restore(self, session_id):
        with self.lock:
            session = self.session_detail(session_id)
            if session is None:
                raise KeyError("会话不存在")
            # 单独导出所选会话，恢复时不混入同项目的其他会话。
            path = self.root / ".aider.session-history" / f"{session['id']}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(session["content"] + "\n", encoding="utf-8")
            return {"command": session["restore_command"]}


def create_server(root, host="127.0.0.1", port=0):
    store = WebStore(root)
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, value, content_type="application/json; charset=utf-8"):
            body = value if isinstance(value, bytes) else _json_bytes(value)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self):
            path = unquote(urlparse(self.path).path)
            if self.command == "GET" and path == "/":
                return self._send(200, HTML.replace("__AIDER_TOKEN__", token).encode("utf-8"),
                                  "text/html; charset=utf-8")
            if self.headers.get("Authorization") != "Bearer " + token:
                return self._send(401, {"error": "unauthorized"})
            data = {}
            if self.command in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 2 * 1024 * 1024:
                    raise ValueError("请求体超过 2 MB")
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("请求体必须是 JSON 对象")
            if path == "/api/providers":
                if self.command == "GET":
                    return self._send(200, store.list_providers())
                if self.command == "POST":
                    return self._send(201, store.save_provider(data))
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "providers"]:
                provider_id = parts[2]
                if self.command == "POST" and parts[3:] == ["test"]:
                    return self._send(200, store.test_provider(provider_id))
                if len(parts) == 3 and self.command == "PUT":
                    return self._send(200, store.save_provider(data, provider_id))
                if len(parts) == 3 and self.command == "DELETE":
                    if not store.delete_provider(provider_id):
                        raise KeyError("模型不存在")
                    return self._send(200, {"ok": True})
            if path == "/api/sessions" and self.command == "GET":
                query = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
                allowed = {k: v for k, v in query.items() if k in (
                    "project", "updated_after", "updated_before", "search", "page", "page_size", "archived", "sort")}
                if "archived" in allowed:
                    allowed["archived"] = _as_bool(allowed["archived"])
                items = store.sessions(**allowed)
                if _as_bool(query.get("include_total")):
                    count_args = {k: v for k, v in allowed.items() if k not in ("page", "page_size")}
                    total = len(store.sessions(**count_args, page_size=None))
                    return self._send(200, {"sessions": items, "total": total})
                return self._send(200, items)
            if len(parts) >= 3 and parts[:2] == ["api", "sessions"]:
                session_id = parts[2]
                if len(parts) == 3 and self.command == "DELETE":
                    return self._send(200, store.delete_session(session_id))
                if len(parts) == 3 and self.command == "GET":
                    result = store.session_detail(session_id)
                    if result is None:
                        raise KeyError("会话不存在")
                    return self._send(200, result)
                if self.command == "POST" and parts[3:] == ["title"]:
                    return self._send(200, store.update_session_title(session_id, data.get("title")))
                if self.command == "POST" and parts[3:] == ["archive"]:
                    return self._send(200, store.archive_session(session_id, data.get("archived")))
                if self.command == "POST" and parts[3:] == ["restore"]:
                    return self._send(200, store.prepare_session_restore(session_id))
            self._send(404, {"error": "not found"})

        def _handle(self):
            try:
                self._dispatch()
            except KeyError as error:
                self._send(404, {"error": str(error)})
            except (ValueError, TypeError) as error:
                self._send(400, {"error": str(error)})
            except OSError:
                self._send(500, {"error": "配置文件读写失败"})

        do_GET = do_POST = do_PUT = do_DELETE = _handle

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.api_token = token
    return server


def serve(root, host="127.0.0.1", port=0, open_browser=True):
    server = create_server(root, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Aider Web 管理页: {url}\n按 Ctrl+C 退出")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def admin(argv=None):
    parser = argparse.ArgumentParser(description="Aider Web 管理页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--root", default=str(Path.cwd()))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    return serve(args.root, args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    admin()
