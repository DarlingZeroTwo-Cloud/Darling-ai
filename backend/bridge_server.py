# -*- coding: utf-8 -*-
"""darling-ai v1.0 桥接服务：本地 HTTP + SSE。

Flutter 前端通过本服务访问 Python 核心（ai_console.py）。
- REST：配置 / 会话 / 模型 / 插件管理
- SSE：流式对话（token / 工具 / 状态 / 确认 / 完成）
- POST /api/decision：按需访问模式下回应命令确认

端口：127.0.0.1:19730
"""
import json
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import ai_console as core
import local_engine
import downloader

HOST, PORT = "127.0.0.1", 19730

JOBS = {}
JOBS_LOCK = threading.Lock()


# ==================== 流式对话（事件化） ====================
def stream_collect(tokens, extra_markers=None, on_visible=None):
    """与 core.stream_reply 相同的块解析逻辑，可见文本通过回调吐出。
    返回 (完整可见文本, 块列表)。"""
    CMD_OPEN, CMD_CLOSE = core.CMD_OPEN, core.CMD_CLOSE
    markers = [("CMD", CMD_OPEN, CMD_CLOSE, None, None)]
    if extra_markers:
        markers += [("P", opn, clo, pname, name)
                    for (pname, name, opn, clo, desc) in extra_markers]
    opens = [m[1] for m in markers]
    closes = [m[2] for m in markers]
    all_marks = opens + closes

    def is_prefix(s):
        return any(m.startswith(s) for m in all_marks)

    def find_earliest(s, needles):
        best = -1
        for n in needles:
            i = s.find(n)
            if i >= 0 and (best < 0 or i < best):
                best = i
        return best

    pending = ""
    cur_kind = None
    cur_open = None
    cur_close = None
    cur_pname = None
    cur_tname = None
    block_buf = ""
    blocks = []
    full = []

    def flush_visible(s):
        if s:
            if on_visible:
                on_visible(s)
            full.append(s)

    def emit_block():
        content = block_buf.strip()
        if not content:
            return
        if cur_kind == "CMD":
            blocks.append(content)
        else:
            blocks.append((cur_pname, cur_tname, content))

    for tok in tokens:
        pending += tok
        while pending:
            if cur_open is None:
                idx = find_earliest(pending, opens)
                if idx >= 0:
                    flush_visible(pending[:idx])
                    for m in markers:
                        if pending[idx:].startswith(m[1]):
                            cur_kind = m[0]
                            cur_open = m[1]
                            cur_close = m[2]
                            cur_pname = m[3]
                            cur_tname = m[4]
                            break
                    pending = pending[idx + len(cur_open):]
                    block_buf = ""
                    continue
                held = 0
                for k in range(1, max(len(m) for m in all_marks)):
                    if is_prefix(pending[-k:]):
                        held = k
                if held:
                    if len(pending) > held:
                        flush_visible(pending[:-held])
                    pending = pending[-held:]
                    break
                flush_visible(pending)
                pending = ""
            else:
                idx = pending.find(cur_close)
                if idx >= 0:
                    block_buf += pending[:idx]
                    pending = pending[idx + len(cur_close):]
                    emit_block()
                    cur_open = cur_close = cur_kind = None
                    cur_pname = cur_tname = None
                    continue
                held = 0
                for k in range(1, len(cur_close)):
                    if cur_close.startswith(pending[-k:]):
                        held = k
                if held:
                    if len(pending) > held:
                        block_buf += pending[:-held]
                    pending = pending[-held:]
                    break
                block_buf += pending
                pending = ""
    if cur_open is not None:
        block_buf += pending
        emit_block()
    elif pending:
        flush_visible(pending)
    return "".join(full), blocks


class Job:
    def __init__(self, cfg, model, text, images=None):
        self.id = uuid.uuid4().hex[:12]
        self.cfg = cfg
        self.model = model
        self.text = text
        self.images = images or []   # [{data_url/b64, mime}, ...]
        self.q = queue.Queue()
        self.decision_evt = threading.Event()
        self.decision_allow = None
        self.finished = threading.Event()


def _emit(job, type_, **kw):
    job.q.put({"type": type_, **kw})


def _mirror_all(cfg, hist):
    """把一份历史镜像到所有模型的 histories（core 按模型读，全共享）。"""
    hist = list(hist)
    for m in cfg.data["models"]:
        cfg.data["histories"][m["id"]] = hist


def _chat_once_stream(job, messages, settings, provider):
    """流式调一次 API：token 事件 + 返回 (可见文本, 块列表)。"""
    try:
        retries = max(0, int(settings.get("api_retries", 1)))
    except Exception:
        retries = 1
    markers = core.plugin_markers_for_request()
    for attempt in range(1 + retries):
        for stream in (True, False):
            try:
                tokens = core.chat_request(
                    provider, job.model["name"], messages, settings, stream=stream)
                return stream_collect(tokens, extra_markers=markers,
                                      on_visible=lambda s: _emit(job, "token", text=s))
            except Exception as e:
                _emit(job, "error", text=core._friendly_error(e))
                if attempt < retries:
                    time.sleep(1)
    return None, None


def stream_agent(job):
    """事件化版本的 ChatWorker.run（3.0：多模态 / 内置工具 / MCP / 容器 / 记忆）。"""
    cfg, model, user_text = job.cfg, job.model, job.text
    settings = cfg.data["settings"]
    provider = cfg.provider(model.get("provider_id"))
    # 本地模型：provider 不在磁盘 config 里（_merge 只在 GET /api/config 时临时合并），
    # 这里根据 model.local_path 现场构造一个 local provider
    if not provider and model.get("local_path"):
        provider = {"id": "local", "name": "本地模型", "type": "local",
                    "base_url": "", "api_key": ""}
    if not provider:
        _emit(job, "error", text="模型对应的供应商不存在，请重新添加。")
        _emit(job, "done", text="", ok=False)
        job.finished.set()
        return
    workspace = settings.get("workspace") or "."
    mode = settings.get("access_mode", "on_demand")
    container_mode = settings.get("container_mode", "off") == "container"

    messages = core.build_messages(cfg, model["id"])
    history = messages[1:]

    # 多模态：文本 + 图片
    user_msg = {"role": "user", "content": user_text}
    if job.images:
        content_parts = []
        if user_text.strip():
            content_parts.append({"type": "text", "text": user_text})
        for img in job.images:
            src = img.get("data_url") or img.get("b64") or ""
            if src:
                content_parts.append({"type": "image_url", "image_url": {"url": src}})
        if content_parts:
            user_msg = {"role": "user", "content": content_parts}
    history.append(user_msg)
    messages.append(user_msg)

    steps = 0
    last_text = ""
    ok_flag = True
    try:
        while steps < core.MAX_AGENT_STEPS:
            steps += 1
            _emit(job, "state", text=f"正在执行第 {steps} 步…" if steps > 1 else "正在连接服务器…")
            if settings.get("task_mode") and steps == 1:
                _emit(job, "step", index=0, total=0, text="任务规划中…")
            reply, blocks = _chat_once_stream(job, messages, settings, provider)
            if reply is None:
                ok_flag = False
                break
            last_text = reply
            messages.append({"role": "assistant", "content": reply})
            history.append(messages[-1])
            if not blocks:
                break
            ran_any = False
            for blk in blocks:
                if isinstance(blk, str):
                    cmd = blk
                    # 容器模式：命令改在容器里执行
                    _emit(job, "tool", title="容器命令" if container_mode else "执行命令",
                          content=cmd, ok=True)
                    should_run = True
                    if mode == "on_demand":
                        job.decision_evt.clear()
                        job.decision_allow = None
                        _emit(job, "ask", cmd=cmd)
                        job.decision_evt.wait(timeout=180)
                        if job.decision_allow is None:
                            messages.append({"role": "user", "content":
                                             f"用户没有回应命令确认：{cmd}，请改用其他方式。"})
                            history.append(messages[-1])
                            continue
                        should_run = job.decision_allow
                    if not should_run:
                        messages.append({"role": "user", "content":
                                         f"用户取消了命令执行：{cmd}，请改用其他方式或询问用户。"})
                        history.append(messages[-1])
                        _emit(job, "tool", title="执行命令", content="用户拒绝执行，已跳过", ok=False)
                        continue
                    try:
                        if container_mode:
                            ok, out = core.PLUGIN_HANDLERS["container_exec"](cmd, workspace)
                        else:
                            ok, out = core.run_command(cmd, workspace)
                    except Exception as e:
                        ok, out = False, f"(命令执行异常: {e})"
                    _emit(job, "tool", title="容器命令" if container_mode else "执行命令",
                          content=out or "(无输出)", ok=ok)
                    result_note = f"[命令执行结果]\n命令: {cmd}\n状态: {'成功' if ok else '失败'}\n输出:\n{out}"
                    messages.append({"role": "user", "content": result_note})
                    history.append(messages[-1])
                    ran_any = True
                else:
                    pname, tname, content = blk
                    handler_fn = None
                    run_kind = "工具"
                    # 1) MCP 工具
                    if str(pname).startswith("MCP::"):
                        server_name = str(pname)[5:]
                        try:
                            import mcp_client
                            args = {}
                            try:
                                parsed = json.loads(content or "{}")
                                if isinstance(parsed, dict):
                                    args = parsed
                                else:
                                    args = {"query": content}
                            except Exception:
                                args = {"query": content}
                            ok, out = mcp_client.call_tool(settings, server_name, tname, args)
                            run_kind = f"MCP · {server_name}/{tname}"
                        except Exception as e:
                            ok, out = False, f"(MCP 调用异常: {e})"
                            run_kind = f"MCP · {server_name}/{tname}"
                    # 2) 内置工具（工作区/浏览器/容器）
                    elif pname == "内置工具" and tname in core.PLUGIN_HANDLERS:
                        handler_fn = core.PLUGIN_HANDLERS[tname]
                        run_kind = f"内置工具 · {tname}"
                    # 3) 插件工具
                    else:
                        for p in core.load_plugins():
                            if p["name"] == pname:
                                for t in p.get("tools", []):
                                    if t.get("name") == tname:
                                        handler_fn = core.PLUGIN_HANDLERS.get(t.get("handler"))
                                        run_kind = f"插件 · {pname}/{tname}"
                                        break
                                break
                    if handler_fn is None and run_kind == "工具":
                        messages.append({"role": "user", "content":
                                         f"[工具调用失败] {pname} 的 {tname} 不存在或已卸载，请勿再调用。"})
                        history.append(messages[-1])
                        _emit(job, "tool", title=f"工具 · {pname}/{tname}",
                              content="工具不存在或已卸载，已跳过", ok=False)
                        continue
                    if handler_fn is not None:
                        try:
                            ok, out = handler_fn(content, workspace)
                        except Exception as e:
                            ok, out = False, f"(工具执行异常: {e})"
                    _emit(job, "tool", title=run_kind, content=out or "(无输出)", ok=ok)
                    result_note = (f"[工具执行结果]\n工具: {run_kind}\n"
                                   f"状态: {'成功' if ok else '失败'}\n结果:\n{out}")
                    messages.append({"role": "user", "content": result_note})
                    history.append(messages[-1])
                    ran_any = True
            if not ran_any:
                break
        cfg.data["histories"][model["id"]] = history[-core.HISTORY_LIMIT:]
        cfg.save()
        # 镜像到所有模型：任何模型切换过来都有完整会话记忆
        _mirror_all(cfg, history[-core.HISTORY_LIMIT:])
        cfg.save()
        # 同步活动会话（前端无需手动回写）
        aid = cfg.data.get("active_conv")
        for c in cfg.data.setdefault("conversations", []):
            if c["id"] == aid:
                c["hist"] = history[-core.HISTORY_LIMIT:]
                c["model_id"] = model["id"]
                if c.get("title", "新对话") == "新对话":
                    for msg in history:
                        if msg.get("role") == "user":
                            c["title"] = msg["content"][:20]
                            break
                cfg.save()
                break
        # 跨会话长期记忆：对话结束后沉淀摘要
        try:
            if settings.get("memory_enabled", True):
                import memory as _memory
                _memory.save_memories(_memory.summarize_history(history))
        except Exception:
            pass
    except Exception as e:
        ok_flag = False
        _emit(job, "error", text=f"对话发生错误: {e}")
        try:
            cfg.data["histories"][model["id"]] = history[-core.HISTORY_LIMIT:]
            cfg.save()
        except Exception:
            pass
    _emit(job, "done", text=last_text, ok=ok_flag)
    job.finished.set()


# ==================== HTTP 服务 ====================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _auth_ok(self):
        """可选 API key 鉴权（仅当用户在设置里设了 key 时生效）。"""
        key = self._cfg().data["settings"].get("api_key", "") or ""
        if not key:
            return True
        return self.headers.get("X-API-Key", "") == key

    def _cfg(self):
        return core.Config()

    def _merge_local_models(self, data):
        """把本地 models 目录下的 gguf 合并成内置 local provider 的可选模型。"""
        locals_ = local_engine.list_local_models()
        providers = data.setdefault("providers", [])
        has_local = any(p.get("id") == "local" for p in providers)
        if not has_local:
            providers.insert(0, {
                "id": "local",
                "name": "本地模型",
                "type": "local",
                "base_url": "",
                "api_key": "",
            })
        # 把本地 gguf 同步进 models 列表（已存在则不重复，保留用户改名）
        existing = {m.get("id") for m in data.setdefault("models", [])}
        for lm in locals_:
            if lm["id"] in existing:
                continue
            data["models"].append({
                "id": lm["id"],
                "name": lm["name"],
                "provider_id": "local",
                "local_path": lm["path"],
                "size_mb": lm["size_mb"],
            })

    # ---- GET ----
    def do_GET(self):
        p = urlparse(self.path)
        if p.path not in ("/api/ping", "/api/stream") and not self._auth_ok():
            self._json(401, {"error": "unauthorized"})
            return
        if p.path == "/api/ping":
            self._json(200, {"ok": True, "version": "3.0.0"})
            return
        if p.path == "/api/config":
            data = self._cfg().data
            # 合并本地扫描到的 GGUF 模型（自动注册为 local provider）
            self._merge_local_models(data)
            self._json(200, data)
            return
        if p.path == "/api/local/catalog":
            self._json(200, {"models": downloader.load_catalog()})
            return
        if p.path == "/api/local/list":
            self._json(200, {"models": local_engine.list_local_models()})
            return
        if p.path == "/api/local/gpu":
            self._json(200, local_engine.detect_gpu())
            return
        if p.path == "/api/local/download/status":
            qs = parse_qs(p.query)
            dl = qs.get("id", [""])[0]
            if dl:
                self._json(200, downloader.status(dl) or {"error": "not found"})
            else:
                self._json(200, {"tasks": downloader.all_status()})
            return
        if p.path == "/api/plugins":
            self._json(200, {"plugins": core.load_plugins()})
            return
        if p.path == "/api/mcp/list":
            settings = self._cfg().data["settings"]
            servers = settings.get("mcp_servers") or {}
            out = {}
            for sname, scfg in servers.items():
                out[sname] = {"command": scfg.get("command", ""),
                              "args": scfg.get("args", []),
                              "enabled": scfg.get("enabled", True),
                              "tools": []}
                try:
                    import mcp_client
                    session = mcp_client.get_session(settings, sname)
                    out[sname]["tools"] = [t.get("name") for t in session.tools_list()]
                except Exception:
                    pass
            self._json(200, {"servers": out})
            return
        if p.path == "/api/memory/list":
            try:
                import memory as _memory
                self._json(200, {"memories": _memory.load_memories(max_per_tag=50, max_total=200)})
            except Exception as e:
                self._json(200, {"memories": [], "error": str(e)})
            return
        if p.path == "/api/container/detect":
            try:
                import container as _container
                self._json(200, _container.detect())
            except Exception as e:
                self._json(200, {"platform": "unknown", "error": str(e)})
            return
        if p.path == "/api/container/distros":
            try:
                import container as _container
                self._json(200, {"distros": _container.wsl_distros()})
            except Exception as e:
                self._json(200, {"distros": [], "error": str(e)})
            return
        if p.path == "/api/stream":
            qs = parse_qs(p.query)
            job_id = qs.get("job", [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json(404, {"error": "job not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while not (job.finished.is_set() and job.q.empty()):
                    try:
                        ev = job.q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    line = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                # 延迟回收 job（10 秒）：前端流中断后还能重连拿到剩余事件与 done
                def _delayed_pop():
                    time.sleep(10)
                    with JOBS_LOCK:
                        JOBS.pop(job.id, None)
                threading.Thread(target=_delayed_pop, daemon=True).start()
            return
        self._json(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        p = urlparse(self.path)
        if not self._auth_ok():
            self._json(401, {"error": "unauthorized"})
            return
        data = self._body()
        if p.path == "/api/config":
            cfg = self._cfg()
            for k in ("providers", "models"):
                if isinstance(data.get(k), list):
                    cfg.data[k] = data[k]
            if isinstance(data.get("settings"), dict):
                cfg.data["settings"].update(data["settings"])
            for k in ("conversations", "active_conv", "histories"):
                if k in data:
                    cfg.data[k] = data[k]
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/provider/delete":
            cfg = self._cfg()
            pid = data.get("provider_id")
            cfg.data["providers"] = [p for p in cfg.data.get("providers", []) if p.get("id") != pid]
            cfg.data["models"] = [m for m in cfg.data.get("models", []) if m.get("provider_id") != pid]
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/convo/new":
            cfg = self._cfg()
            mid = data.get("model_id")
            convs = cfg.data.setdefault("conversations", [])
            cid = uuid.uuid4().hex[:8]
            convs.insert(0, {"id": cid, "title": "新对话", "created": time.time(),
                             "hist": [], "model_id": mid})
            cfg.data["active_conv"] = cid
            _mirror_all(cfg, [])
            cfg.save()
            self._json(200, {"conv_id": cid})
            return
        if p.path == "/api/convo/load":
            cfg = self._cfg()
            conv_id = data.get("conv_id")
            convs = cfg.data.setdefault("conversations", [])
            for c in convs:
                if c["id"] == conv_id:
                    cfg.data["active_conv"] = conv_id
                    _mirror_all(cfg, c.get("hist", []))
                    cfg.save()
                    self._json(200, {"model_id": c.get("model_id"), "hist": c.get("hist", [])})
                    return
            self._json(404, {"error": "conversation not found"})
            return
        if p.path == "/api/convo/delete":
            cfg = self._cfg()
            conv_id = data.get("conv_id")
            convs = cfg.data.setdefault("conversations", [])
            cfg.data["conversations"] = [c for c in convs if c["id"] != conv_id]
            if cfg.data.get("active_conv") == conv_id:
                cfg.data["active_conv"] = None
                _mirror_all(cfg, [])
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/chat":
            cfg = self._cfg()
            model = cfg.model(data.get("model_id", ""))
            if not model:
                self._json(400, {"error": "model not found"})
                return
            job = Job(cfg, model, data.get("text", ""), images=data.get("images") or [])
            with JOBS_LOCK:
                JOBS[job.id] = job
            threading.Thread(target=stream_agent, args=(job,), daemon=True).start()
            self._json(200, {"job": job.id})
            return
        if p.path == "/api/decision":
            job_id = data.get("job")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job:
                job.decision_allow = bool(data.get("allow"))
                job.decision_evt.set()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/provider/fetch":
            cfg = self._cfg()
            prov = cfg.provider(data.get("provider_id", ""))
            if not prov:
                self._json(400, {"error": "provider not found"})
                return
            try:
                timeout = max(10, int(cfg.data["settings"].get("connect_timeout", 15)) + 15)
                models = core.fetch_models(prov, timeout=timeout)
                self._json(200, {"models": models})
            except Exception as e:
                self._json(200, {"models": [], "error": core._friendly_error(e)})
            return
        if p.path == "/api/plugin/import":
            cfg = self._cfg()
            path = data.get("path")
            content = data.get("content")
            try:
                if content:
                    parsed = json.loads(content)
                    ok_v, err_v = core.validate_plugin(parsed)
                    if not ok_v:
                        self._json(200, {"ok": False, "error": err_v})
                        return
                    fname = parsed.get("id", uuid.uuid4().hex[:8]) + ".json"
                    with open(os.path.join(core.plugins_dir(), fname), "w", encoding="utf-8") as f:
                        json.dump(parsed, f, ensure_ascii=False, indent=2)
                elif path:
                    if not os.path.exists(path):
                        self._json(200, {"ok": False, "error": "文件不存在"})
                        return
                    with open(path, "r", encoding="utf-8") as f:
                        parsed = json.load(f)
                    ok_v, err_v = core.validate_plugin(parsed)
                    if not ok_v:
                        self._json(200, {"ok": False, "error": err_v})
                        return
                    fname = parsed.get("id", uuid.uuid4().hex[:8]) + ".json"
                    import shutil
                    shutil.copy(path, os.path.join(core.plugins_dir(), fname))
                else:
                    self._json(200, {"ok": False, "error": "缺少 path 或 content"})
                    return
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(200, {"ok": False, "error": f"导入失败: {e}"})
            return
        if p.path == "/api/plugin/delete":
            pid = data.get("id")
            target = None
            for pname in ("id", "name"):
                v = data.get(pname)
                if v:
                    target = v
                    break
            if not target:
                self._json(200, {"ok": False, "error": "缺少插件 id"})
                return
            removed = False
            for fn in os.listdir(core.plugins_dir()):
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(core.plugins_dir(), fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        pl = json.load(f)
                except Exception:
                    continue
                if pl.get("id") == target or pl.get("name") == target:
                    os.remove(fp)
                    removed = True
            self._json(200, {"ok": removed})
            return
        if p.path == "/api/plugin/toggle":
            cfg = self._cfg()
            name = data.get("name")
            tool = data.get("tool")
            enabled = bool(data.get("enabled"))
            dis = set(cfg.data["settings"].get("disabled_tools", []))
            key = f"{name}::{tool}"
            if enabled:
                dis.discard(key)
            else:
                dis.add(key)
            cfg.data["settings"]["disabled_tools"] = sorted(dis)
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/local/download/start":
            cfg = self._cfg()
            try:
                ret = downloader.start_download(data.get("catalog_id", ""),
                                                cfg.data["settings"])
                self._json(200, ret)
            except Exception as e:
                self._json(200, {"status": "error", "error": str(e)})
            return
        if p.path == "/api/local/delete":
            path = data.get("path", "")
            ok = downloader.delete_local_model(path)
            self._json(200, {"ok": ok})
            return
        if p.path == "/api/local/download/pause":
            downloader.pause(data.get("id", ""))
            self._json(200, {"ok": True})
            return
        if p.path == "/api/local/download/resume":
            downloader.resume(data.get("id", ""))
            self._json(200, {"ok": True})
            return
        if p.path == "/api/local/download/cancel":
            downloader.cancel(data.get("id", ""))
            self._json(200, {"ok": True})
            return
        if p.path == "/api/local/import":
            src = data.get("path", "")
            if not src or not os.path.exists(src):
                self._json(200, {"ok": False, "error": "文件不存在"})
                return
            if not src.lower().endswith(".gguf"):
                self._json(200, {"ok": False, "error": "只支持 .gguf 文件"})
                return
            try:
                import shutil
                dest_dir = local_engine.models_dir()
                dest = os.path.join(dest_dir, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dest):
                    shutil.copy2(src, dest)
                self._json(200, {"ok": True, "path": dest})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        # ---- 3.0 新端点 ----
        if p.path == "/api/mcp/save":
            cfg = self._cfg()
            name = str(data.get("name", "")).strip()
            if not name:
                self._json(200, {"ok": False, "error": "缺少 MCP server 名称"})
                return
            servers = cfg.data["settings"].setdefault("mcp_servers", {})
            servers[name] = {
                "command": str(data.get("command", "")).strip(),
                "args": [str(a) for a in (data.get("args") or [])],
                "env": data.get("env") or {},
                "enabled": True if data.get("enabled") is None else bool(data.get("enabled")),
            }
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/mcp/delete":
            cfg = self._cfg()
            name = str(data.get("name", "")).strip()
            servers = cfg.data["settings"].setdefault("mcp_servers", {})
            servers.pop(name, None)
            cfg.save()
            self._json(200, {"ok": True})
            return
        if p.path == "/api/mcp/test":
            cfg = self._cfg()
            name = str(data.get("name", "")).strip()
            try:
                import mcp_client
                session = mcp_client.get_session(cfg.data["settings"], name)
                tools = session.tools_list()
                self._json(200, {"ok": True,
                                 "tools": [t.get("name") for t in tools]})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/memory/clear":
            try:
                import memory as _memory
                _memory.clear_memories(data.get("tag"))
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/container/install-docker":
            try:
                import container as _container
                ok, out = _container.install_docker()
                self._json(200, {"ok": ok, "detail": out})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/container/exec":
            cfg = self._cfg()
            settings = cfg.data["settings"]
            command = str(data.get("command", "")).strip()
            try:
                import container as _container
                if _container.is_windows():
                    target = data.get("target") or settings.get("container_target") or ""
                    ok, out = _container.wsl_exec(target, command,
                                                  workspace=settings.get("workspace"))
                else:
                    image = data.get("target") or settings.get("container_target") or \
                            settings.get("container_image") or "ubuntu:latest"
                    ok, out = _container.docker_exec(image, command,
                                                     workspace=settings.get("workspace"))
                self._json(200, {"ok": ok, "output": out})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/browser/open":
            url = str(data.get("url", "")).strip()
            try:
                import workspace_tools
                ok, out = workspace_tools.handler_browser_open(url)
                self._json(200, {"ok": ok, "text": out})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/browser/screenshot":
            url = str(data.get("url", "")).strip()
            try:
                import workspace_tools
                out_path = os.path.join(core.app_dir(), "shot.png")
                res = workspace_tools.browser_screenshot(url, out_path)
                if res:
                    b64 = workspace_tools.image_b64(res)
                    self._json(200, {"ok": True, "image": "data:image/jpeg;base64," + b64})
                else:
                    self._json(200, {"ok": False,
                                     "error": "未安装 Playwright 浏览器引擎（可选功能），可用文本浏览。"})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)})
            return
        if p.path == "/api/convo/export":
            cfg = self._cfg()
            conv_id = data.get("conv_id")
            convs = cfg.data.setdefault("conversations", [])
            for c in convs:
                if c["id"] == conv_id:
                    hist = c.get("hist", [])
                    lines = [f"# {c.get('title', '对话')}", ""]
                    for m in hist:
                        role = "用户" if m.get("role") == "user" else "助手"
                        content = m.get("content")
                        if not isinstance(content, str):
                            continue
                        if content.startswith("[命令执行结果]") or content.startswith("[工具执行结果]"):
                            continue
                        lines.append(f"## {role}\n\n{content}\n")
                    md = "\n".join(lines)
                    self._json(200, {"ok": True,
                                     "title": c.get("title", "对话"),
                                     "markdown": md,
                                     "json": json.dumps(hist, ensure_ascii=False, indent=2)})
                    return
            self._json(404, {"error": "conversation not found"})
            return
        self._json(404, {"error": "not found"})


def main():
    core.ensure_default_plugins()
    # 启动自愈：清理上次强杀残留的 .part 临时文件（避免损坏文件占着磁盘）
    try:
        md = local_engine.models_dir()
        for fn in os.listdir(md):
            if fn.endswith(".part"):
                try:
                    os.remove(os.path.join(md, fn))
                except OSError:
                    pass
    except Exception:
        pass
    cfg = core.Config()
    host = cfg.data["settings"].get("bind_host", "127.0.0.1") or "127.0.0.1"
    try:
        server = ThreadingHTTPServer((host, PORT), Handler)
    except OSError as e:
        print(f"port {PORT} unavailable: {e}")
        return
    print(f"darling-ai bridge listening on http://{host}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
