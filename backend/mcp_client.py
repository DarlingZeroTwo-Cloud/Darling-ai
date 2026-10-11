"""darling-ai 3.0 MCP 客户端（stdio 传输，纯标准库实现，无第三方依赖）。

支持配置多个 MCP server（command + args + env），通过 JSON-RPC 2.0 over stdio
与 server 通信：initialize -> tools/list -> tools/call。
AI 在回复中输出 [[MCP::<server>::<tool>]]JSON参数[[/MCP]] 即可调用。
"""
import json
import os
import subprocess
import threading

PROTOCOL_VERSION = "2025-03-26"
_STARTUP_TIMEOUT = 15.0
_CALL_TIMEOUT = 60.0


class McpError(Exception):
    pass


class McpSession:
    """一个 MCP server 的 stdio 会话（进程级单例，按 server 名复用）。"""

    _instances = {}
    _lock = threading.Lock()

    def __init__(self, server_cfg):
        self.name = server_cfg.get("name") or server_cfg.get("id") or "mcp"
        self.command = server_cfg.get("command") or "echo"
        self.args = server_cfg.get("args") or []
        self.env = dict(os.environ)
        env_extra = server_cfg.get("env") or {}
        if isinstance(env_extra, dict):
            self.env.update({str(k): str(v) for k, v in env_extra.items()})
        self.proc = None
        self._write_lock = threading.Lock()
        self._id = 0
        self._readers = {}  # id -> threading.Event
        self._responses = {}  # id -> dict

    # ---- 进程管理 ----
    def _ensure_proc(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        try:
            self.proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as e:
            raise McpError(f"MCP server「{self.name}」启动失败：找不到命令 {self.command!r}") from e
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _reader_loop(self):
        """读 stdout 逐行，按 id 分发 JSON-RPC 响应。"""
        try:
            for raw in self.proc.stdout:
                if not raw or not raw.strip():
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                if rid in self._responses:
                    self._responses[rid] = msg
                    ev = self._readers.pop(rid, None)
                    if ev:
                        ev.set()
        except Exception:
            pass
        for rid, ev in list(self._readers.items()):
            self._responses[rid] = {"error": {"code": -32000,
                                              "message": "MCP server 已退出"}}
            ev.set()

    def _request(self, method, params=None, timeout=None):
        self._ensure_proc()
        if self.proc.poll() is not None:
            raise McpError(f"MCP server「{self.name}」进程已退出(code={self.proc.returncode})")
        rid = self._next_id()
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        ev = threading.Event()
        self._responses[rid] = None
        self._readers[rid] = ev
        try:
            with self._write_lock:
                self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
        except Exception as e:
            self._readers.pop(rid, None)
            self._responses.pop(rid, None)
            raise McpError(f"MCP 请求写入失败: {e}") from e
        if not ev.wait(timeout=timeout or _CALL_TIMEOUT):
            self._readers.pop(rid, None)
            self._responses.pop(rid, None)
            raise McpError(f"MCP server「{self.name}」响应超时（{method}）")
        resp = self._responses.pop(rid, None)
        if resp is None:
            raise McpError(f"MCP server「{self.name}」无响应")
        if "error" in resp:
            err = resp["error"]
            raise McpError(f"MCP 调用 {method} 失败: {err.get('message', err)}")
        return resp.get("result") or {}

    def _next_id(self):
        with self._write_lock:
            self._id += 1
            return self._id

    # ---- 公开方法 ----
    def initialize(self):
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "darling-ai", "version": "3.0.0"},
        }, timeout=_STARTUP_TIMEOUT)
        try:
            self._request("notifications/initialized", None, timeout=5)
        except McpError:
            pass
        return True

    def tools_list(self):
        res = self._request("tools/list", {}, timeout=_STARTUP_TIMEOUT)
        return res.get("tools") or []

    def tools_call(self, name, arguments):
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}},
                            timeout=_CALL_TIMEOUT)
        content = res.get("content") or []
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "image":
                    parts.append("[图片数据]")
                else:
                    parts.append(str(item.get("text", json.dumps(item, ensure_ascii=False))))
            else:
                parts.append(str(item))
        is_error = bool(res.get("isError"))
        return is_error, "\n".join(parts)

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


# ==================== 全局会话管理 ====================
_sessions = {}


def get_session(cfg_dict, server_name):
    """按配置启动/复用 MCP 会话。cfg_dict: settings 里的 mcp_servers dict。"""
    with McpSession._lock:
        servers = cfg_dict.get("mcp_servers") or {}
        server_cfg = servers.get(server_name)
        if not server_cfg:
            raise McpError(f"MCP server「{server_name}」未配置")
        server_cfg = dict(server_cfg)
        server_cfg["name"] = server_name
        session = _sessions.get(server_name)
        if session is None:
            session = McpSession(server_cfg)
            try:
                session.initialize()
            except McpError:
                try:
                    session.close()
                except Exception:
                    pass
                raise
            _sessions[server_name] = session
        return session


def all_markers(cfg_dict):
    """构造可注入 system prompt 的 MCP 工具标记列表。

    返回: [(插件名, 工具名, 开, 闭, 说明)]
    """
    servers = (cfg_dict.get("mcp_servers") or {})
    markers = []
    for sname, scfg in servers.items():
        if not isinstance(scfg, dict):
            continue
        if scfg.get("enabled") is False:
            continue
        try:
            session = get_session(cfg_dict, sname)
            tools = session.tools_list()
        except McpError:
            continue
        for t in tools:
            tname = t.get("name", "")
            desc = (t.get("description") or tname) or tname
            markers.append((f"MCP::{sname}", tname,
                            f"[[MCP::{sname}::{tname}]]",
                            "[[/MCP]]", desc))
    return markers


def call_tool(cfg_dict, server_name, tool_name, arguments):
    """执行 MCP 工具，返回 (ok, output)。"""
    try:
        session = get_session(cfg_dict, server_name)
        is_error, text = session.tools_call(tool_name, arguments)
        return (not is_error), text
    except McpError as e:
        return False, str(e)
    except Exception as e:
        return False, f"(MCP 调用异常: {e})"


def close_all():
    for s in list(_sessions.values()):
        try:
            s.close()
        except Exception:
            pass
    _sessions.clear()
