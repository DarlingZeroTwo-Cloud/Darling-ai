#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 控制台助手 (AI Console Assistant) v0.1.0
============================================
一个无 GUI 的命令行 AI 助手：

  1. 连接已有模型 —— 选择模型后进入对话，支持流式输出、工作区命令执行
  2. 添加供应商   —— 内置 OpenAI / DeepSeek / 通义千问 / 智谱GLM / Kimi / 硅基流动 / Ollama，
                     也支持自定义供应商（填 API 地址 + API Key，
                     可选择填的地址是否为"完整接口地址"）
  3. 设置         —— AI 访问模式（完全访问 / 按需访问）、温度、最大 tokens、
                     系统提示词、工作区、供应商与模型管理、对话历史清理

特性：
  - 配置保存在程序同目录 config.json（可用环境变量 AI_CONSOLE_CONFIG 覆盖路径）
  - 工作区：AI 可以在你选定的目录里执行命令
      * 按需访问：每次执行前询问你是否允许
      * 完全访问：自动执行（请谨慎使用，AI 可执行任意命令）
  - 对话历史按模型自动保存

运行：
  python ai_console.py
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid


# ---- 控制台初始化：切到 UTF-8 代码页 + 按 UTF-8 输出（解决中文乱码）----
# 用 ctypes 直接调用 Win32 API，避免 os.system("chcp") 派生 cmd.exe 吃掉标准输入
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # CP_UTF8
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import requests
except ImportError:
    print("缺少依赖库 requests，请先执行: pip install requests")
    sys.exit(1)


# ---- 启用控制台 VT100 颜色转义（Win10+ conhost / PowerShell 原生支持）----
def _enable_vt():
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        k.GetConsoleMode(h, ctypes.byref(mode))
        k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False


VT_OK = _enable_vt()


def _system_is_light_theme():
    """读取注册表判断 Windows 浅色/深色主题。"""
    if os.name != "nt":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return val == 1
    except Exception:
        return False


LIGHT_THEME = _system_is_light_theme()

# ==================== 颜色 ====================
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = ""
    RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = ""


def _init_colors():
    """按系统主题 + VT 支持情况配置调色板。"""
    if not VT_OK:
        # 老控制台不支持 ANSI：全部置空，降级为纯文本，避免出现 [32m 乱码
        return
    if LIGHT_THEME:
        C.DIM = "\033[90m"
        C.RED = "\033[31m"     # 深红，浅底上可读
        C.GREEN = "\033[32m"
        C.YELLOW = "\033[33m"
        C.BLUE = "\033[34m"
        C.MAGENTA = "\033[35m"
        C.CYAN = "\033[36m"
        C.WHITE = "\033[30m"   # 浅底上正文用黑
    else:
        C.DIM = "\033[90m"
        C.RED = "\033[91m"     # 亮红，深底上醒目
        C.GREEN = "\033[92m"
        C.YELLOW = "\033[93m"
        C.BLUE = "\033[94m"
        C.MAGENTA = "\033[95m"
        C.CYAN = "\033[96m"
        C.WHITE = "\033[97m"   # 深底上正文用亮白


_init_colors()


def col(text, code):
    return f"{code}{text}{C.RESET}"


def clear_screen():
    """清屏（菜单切换时使用，让每个界面干净独立）。
    使用 ANSI 转义序列而非 os.system("cls")，避免在管道/重定向环境下
    误吃后续标准输入。"""
    if VT_OK:
        print("\033[2J\033[H", end="", flush=True)
    else:
        os.system("cls" if os.name == "nt" else "clear")


# ==================== 常量 ====================
APP_NAME = "AI 控制台助手"
APP_VERSION = "0.3.0"
MAX_AGENT_STEPS = 8          # 一次用户提问后，AI 最多连续执行的命令轮数
MAX_OUTPUT_CHARS = 20000     # 命令输出回传给 AI 的最大字符数
HISTORY_LIMIT = 60           # 每个模型保存的历史消息条数上限
REQUEST_TIMEOUT = (10, 300)  # (连接超时, 读取超时)

# 内置供应商（自带 API 地址，只需填 Key）
DEFAULT_PROVIDERS = [
    {"id": "openai",      "name": "OpenAI",           "base_url": "https://api.openai.com/v1"},
    {"id": "deepseek",    "name": "DeepSeek",         "base_url": "https://api.deepseek.com/v1"},
    {"id": "qwen",        "name": "通义千问(阿里云)",  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"id": "zhipu",       "name": "智谱 GLM",         "base_url": "https://open.bigmodel.cn/api/paas/v4"},
    {"id": "kimi",        "name": "Moonshot Kimi",    "base_url": "https://api.moonshot.cn/v1"},
    {"id": "siliconflow", "name": "硅基流动",         "base_url": "https://api.siliconflow.cn/v1"},
    {"id": "ollama",      "name": "Ollama(本地)",     "base_url": "http://localhost:11434/v1"},
]

DEFAULT_SYSTEM_PROMPT = (
    "你是运行在用户电脑上的 AI 助手 darling-ai，通过 GUI 与用户交流。\n"
    "你拥有在用户指定的\"工作区\"目录里执行命令的能力。当你需要查看文件、"
    "运行程序、处理数据等实际操作时，请输出如下格式的命令块：\n"
    "[[CMD]]在这里写要执行的命令[[/CMD]]\n"
    "命令会在用户电脑上真实执行，请务必小心谨慎，不要执行任何可能造成破坏"
    "或危险的命令。执行结果会自动返回给你，你可以根据结果继续工作。\n"
    "如果不需要执行命令，直接正常回答用户即可。\n"
    "【工作区工具】你还可以使用文件读写、目录浏览、git 操作与网页浏览工具"
    "（见下方可用工具标记），优先用工具而非命令来完成文件类操作。"
)

DEFAULT_SETTINGS = {
    "access_mode": "on_demand",  # full=完全访问(自动执行) / on_demand=按需访问(每次询问)
    "temperature": 0.7,
    "max_tokens": 4096,
    "reasoning_effort": "off",   # off=不发送 / low / medium / high（思考强度，仅支持的模型生效）
    "top_p": 1.0,
    "connect_timeout": 15,       # 连接超时（秒）
    "api_retries": 1,            # API 请求失败后的额外重试次数
    "read_timeout": 300,         # 读取超时（秒），防长时间无响应
    "auto_reconnect": True,      # 流式中断自动重连
    "use_proxy": False,          # 是否使用系统代理（默认关，避免代理残留导致卡死）
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "workspace": None,           # 首次运行时自动设为程序所在目录
    # ---- 3.0 新增 ----
    "context_auto_compress": True,   # 上下文超限自动压缩（滑动窗口）
    "context_max_tokens": 24000,     # 压缩阈值（估算 token 数）
    "memory_enabled": True,          # 跨会话长期记忆
    "task_mode": False,              # 任务模式：AI 先规划多步再逐步执行
    "browser_enabled": True,         # 内置浏览器工具（读网页）
    "workspace_tools": True,         # 工作区工具（文件读写/git）
    "container_mode": "off",         # off=直接操作本机 / container=在容器里工作
    "container_target": "",          # 容器目标：WSL 发行版名 或 docker 镜像名
    "container_image": "ubuntu:latest",
    "mcp_servers": {},               # MCP server 配置: {名称: {command,args,env,enabled}}
}


def app_dir():
    """程序所在目录（打包成 exe 后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    """配置文件路径。打包成 exe 后写到 %APPDATA%\\darling-ai\\，避免 Program Files 无写权限。"""
    if os.environ.get("AI_CONSOLE_CONFIG"):
        return os.environ["AI_CONSOLE_CONFIG"]
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "darling-ai")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")
    return os.path.join(app_dir(), "config.json")


def plugins_dir():
    """插件目录 = 配置文件所在目录下的 plugins 子目录。"""
    d = os.path.join(os.path.dirname(config_path()), "plugins")
    os.makedirs(d, exist_ok=True)
    return d


# ==================== 配置 ====================
class Config:
    def __init__(self):
        self.data = {
            "providers": [],
            "models": [],
            "settings": dict(DEFAULT_SETTINGS),
            "histories": {},
        }
        self.load()

    def load(self):
        path = config_path()
        if not os.path.exists(path):
            self.data["settings"]["workspace"] = app_dir()
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for key in ("providers", "models", "histories", "conversations"):
                if isinstance(loaded.get(key), list if key != "histories" else dict):
                    self.data[key] = loaded[key]
            if "active_conv" in loaded and isinstance(loaded["active_conv"], (str, type(None))):
                self.data["active_conv"] = loaded["active_conv"]
            settings = dict(DEFAULT_SETTINGS)
            settings.update(loaded.get("settings", {}))
            if not settings.get("workspace"):
                settings["workspace"] = app_dir()
            self.data["settings"] = settings
        except Exception as e:
            print(col(f"[警告] 配置文件读取失败({e})，已使用默认配置。", C.YELLOW))
            self.data["settings"]["workspace"] = app_dir()

    def save(self):
        try:
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(col(f"[错误] 保存配置失败: {e}", C.RED))

    # ---- 便捷方法 ----
    def provider(self, pid):
        for p in self.data["providers"]:
            if p["id"] == pid:
                return p
        return None

    def model(self, mid):
        for m in self.data["models"]:
            if m["id"] == mid:
                return m
        return None

    def history(self, model_id):
        return self.data["histories"].get(model_id, [])


# ==================== 插件系统 ====================
PLUGIN_HANDLERS = {}   # handler 名 -> 函数(内容, 工作区) -> (是否成功, 输出文本)

# 插件 JSON 格式说明（注入系统提示词，让 AI 自己会写插件）
# handler 只能从 PLUGIN_HANDLERS 注册表中选；写插件前先看清楚每个 handler 的用途
PLUGIN_FORMAT_DOC = """插件 JSON 格式（每个插件是一个 .json 文件，放在插件目录）：
{
  "id": "插件唯一ID（英文小写字母/数字/连字符，如 my-news）",
  "name": "插件显示名称",
  "version": "1.0.0",
  "description": "插件作用描述",
  "author": "作者名",
  "tools": [
    {
      "name": "工具名（英文）",
      "description": "工具作用与用法说明",
      "pattern": "[[开标记]]",
      "close": "[[闭标记]]",
      "handler": "必须是下方列出的内置处理器名之一",
      "enabled": true
    }
  ]
}
每个插件可以定义多个工具；AI 在回复中输出 开标记+内容+闭标记 即可调用工具，
程序会执行并把结果回传给你。

【可用 handler 列表】（写插件时 handler 字段只能填下面这些，填别的会校验失败）：
- web_search：联网搜索，内容=搜索关键词。返回必应搜索结果（标题+URL+摘要），结果自动存数据库。
- web_search_deep：深度联网搜索，内容=搜索关键词。搜索后自动打开前几条结果网页抓正文，返回带正文的详细结果，自动存数据库。适合需要深入阅读资料时。
- fetch_page：抓取指定网页正文，内容=完整 URL（必须以 http:// 或 https:// 开头）。返回该网页正文文本，自动存数据库。
- make_plugin：插件工坊，内容=完整插件 JSON（本格式），程序会校验并自动安装。
- file_read：读取工作区内文件，内容=文件路径（相对工作区，禁止越界）。返回文件文本。
- file_write：写入工作区内文件，内容格式=第一行文件路径，换行后是文件内容。
- dir_list：列出目录内容，内容=目录路径（默认当前工作区）。
- git_status：查看 git 工作区状态，内容可留空。
- git_diff：查看 git 改动，内容=可选文件路径。
- git_commit：提交 git 改动，内容=提交信息。
- browser_open：内置浏览器打开网页，内容=URL。返回网页正文。
- container_exec：在容器（Windows 用 WSL / Linux 用 Docker）中执行命令，内容=要在容器里执行的命令。

注意：pattern 和 close 必须是成对的标记，不能与已有插件的标记冲突（比如已经有 [[SEARCH]]，新插件就别再用）。
插件 id 不能和已存在插件 id 重复（重复会覆盖）。"""

DEFAULT_PLUGINS = [
    {
        "id": "web-search",
        "name": "联网搜索",
        "version": "1.1.0",
        "description": "让 AI 联网搜索实时信息（必应），结果自动存数据库",
        "author": "darling",
        "tools": [
            {
                "name": "search",
                "description": "快速搜索：在回复中输出 [[SEARCH]]要搜索的内容[[/SEARCH]]。返回标题+URL+摘要。",
                "pattern": "[[SEARCH]]",
                "close": "[[/SEARCH]]",
                "handler": "web_search",
                "enabled": True,
            },
            {
                "name": "deep_search",
                "description": "深度搜索：在回复中输出 [[DSEARCH]]要搜索的内容[[/DSEARCH]]。自动打开前几条结果网页抓正文，返回带正文的详细结果。需要深入了解某主题时用这个。",
                "pattern": "[[DSEARCH]]",
                "close": "[[/DSEARCH]]",
                "handler": "web_search_deep",
                "enabled": True,
            },
            {
                "name": "fetch",
                "description": "抓取指定网页正文：在回复中输出 [[FETCH]]完整URL[[/FETCH]]。返回该网页的正文文本。",
                "pattern": "[[FETCH]]",
                "close": "[[/FETCH]]",
                "handler": "fetch_page",
                "enabled": True,
            },
        ],
    },
    {
        "id": "make-plugin",
        "name": "插件工坊",
        "version": "1.0.0",
        "description": "让 AI 自己编写并自动安装新插件",
        "author": "darling",
        "tools": [
            {
                "name": "make_plugin",
                "description": "根据用户需求编写新插件。把符合插件格式的 JSON 放在 [[MAKE_PLUGIN]] 和 [[/MAKE_PLUGIN]] 之间，程序会校验并自动安装",
                "pattern": "[[MAKE_PLUGIN]]",
                "close": "[[/MAKE_PLUGIN]]",
                "handler": "make_plugin",
                "enabled": True,
            }
        ],
    },
]


def validate_plugin(data):
    """校验插件数据，返回 (是否合法, 错误信息)。"""
    if not isinstance(data, dict):
        return False, "插件不是 JSON 对象"
    pid = str(data.get("id") or "").strip()
    name = str(data.get("name") or "").strip()
    if not pid or not name:
        return False, "插件缺少 id 或 name 字段"
    if not re.fullmatch(r"[A-Za-z0-9\-]+", pid):
        return False, f"插件 id 只能包含英文/数字/连字符，当前: {pid}"
    tools = data.get("tools")
    if not isinstance(tools, list) or not tools:
        return False, "插件缺少 tools 列表（至少一个工具）"
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            return False, f"第 {i+1} 个工具不是 JSON 对象"
        for field in ("name", "description", "pattern", "close", "handler"):
            if not str(t.get(field) or "").strip():
                return False, f"工具 {i+1} 缺少字段: {field}"
        if t.get("handler") not in PLUGIN_HANDLERS:
            available = ", ".join(sorted(PLUGIN_HANDLERS.keys()))
            return False, (
                f"工具 {t.get('name')} 的处理器 '{t.get('handler')}' 不存在。"
                f"当前可用 handler 只有：{available}。请从这里面选一个，不要自己发明。"
            )
    return True, ""


def load_plugins():
    """加载插件目录下所有 .json 插件，返回插件字典列表。"""
    plugins = []
    d = plugins_dir()
    try:
        names = sorted(os.listdir(d))
    except Exception:
        return plugins
    for fn in names:
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            ok, err = validate_plugin(data)
            if not ok:
                print(col(f"[警告] 插件 {fn} 格式无效，已跳过: {err}", C.YELLOW))
                continue
            data["_file"] = fn
            plugins.append(data)
        except Exception as e:
            print(col(f"[警告] 插件 {fn} 读取失败: {e}", C.YELLOW))
    return plugins


def ensure_default_plugins():
    """首次运行写入默认插件；内置默认插件版本升级时自动覆盖更新。"""
    d = plugins_dir()
    for p in DEFAULT_PLUGINS:
        fn = p["id"] + ".json"
        path = os.path.join(d, fn)
        need_write = True
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                # 已存在：仅当旧版本号 < 内置版本号 才覆盖升级
                old_v = str(old.get("version") or "0.0.0")
                if old_v >= p["version"]:
                    need_write = False
            except Exception:
                need_write = True
        if need_write:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(p, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(col(f"[警告] 默认插件 {fn} 写入失败: {e}", C.YELLOW))


def enabled_plugin_markers():
    """收集所有启用插件的 (工具名, 开标记, 闭标记, 说明) 列表，用于解析与提示注入。"""
    markers = []
    for p in load_plugins():
        for t in p.get("tools", []):
            if not t.get("enabled", True):
                continue
            markers.append((p["name"], t["name"], t["pattern"], t["close"], t["description"]))
    return markers


# ==================== API 调用 ====================
def http_session(settings=None, timeout=None):
    """创建 requests 会话。

    - 默认 trust_env=False：不读系统代理/环境变量代理，避免关闭 VPN/代理后
      残留的代理设置让请求一直卡在死代理上（表现为“等待服务器响应”）。
    - 若用户在设置里开启“使用系统代理”，则恢复读系统代理。
    """
    s = requests.Session()
    use_proxy = bool(settings and settings.get("use_proxy"))
    s.trust_env = use_proxy
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    return s


def _req_timeout(settings=None):
    """从设置读连接/读取超时；读取超时固定 300s（长回复足够）。"""
    conn = 15
    if settings:
        try:
            conn = max(3, int(settings.get("connect_timeout", 15)))
        except Exception:
            conn = 15
    return (conn, 300)


def chat_url(provider):
    base = (provider.get("base_url") or "").strip().rstrip("/")
    if provider.get("full_endpoint"):
        return base  # 完整接口地址，直接使用
    return base + "/chat/completions"


def auth_headers(provider):
    headers = {"Content-Type": "application/json"}
    key = (provider.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers


def fetch_models(provider, timeout=30):
    """拉取供应商的模型列表，返回模型 id 列表。失败抛出异常。"""
    base = (provider.get("base_url") or "").strip().rstrip("/")
    if provider.get("full_endpoint"):
        # 完整地址模式下，去掉 /chat/completions 后缀以推导 models 接口
        for suffix in ("/chat/completions", "/v1/chat/completions"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    else:
        # 普通模式下用户也可能误填了完整地址，自动修正
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
    url = base + "/models"
    resp = http_session().get(url, headers=auth_headers(provider), timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    ids = []
    for m in data.get("data", []):
        mid = m.get("id")
        if mid:
            ids.append(mid)
    return ids


def chat_request(provider, model, messages, settings, stream=True):
    """发起对话请求。stream=True 时生成 content 增量 token；否则生成单个完整文本。"""
    # 本地引擎：provider.type == "local"，走 GGUF 推理
    if provider.get("type") == "local":
        import local_engine
        path = provider.get("local_path", "")
        if not path or not os.path.exists(path):
            raise RuntimeError("本地模型文件不存在，请在模型市场重新下载。")
        yield from local_engine.chat_stream(messages, settings, path)
        return
    url = chat_url(provider)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(settings.get("temperature", 0.7)),
        "max_tokens": int(settings.get("max_tokens", 4096)),
        "stream": stream,
    }
    # 思考强度：仅当用户显式设置时才发送，避免不支持该参数的模型报错
    effort = str(settings.get("reasoning_effort") or "off").strip().lower()
    if effort in ("low", "medium", "high"):
        payload["reasoning_effort"] = effort
    # top_p
    try:
        tp = float(settings.get("top_p", 1.0))
        if 0.0 < tp <= 1.0 and tp < 1.0:   # 默认 1.0 不发送，等价于默认行为
            payload["top_p"] = tp
    except Exception:
        pass
    sess = http_session(settings)
    resp = sess.post(
        url, headers=auth_headers(provider), json=payload,
        timeout=_req_timeout(settings), stream=stream,
    )
    if resp.status_code != 200:
        detail = ""
        try:
            err = resp.json().get("error", "")
            if isinstance(err, dict):
                detail = err.get("message", "")
            else:
                detail = str(err)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {resp.status_code} {detail}".strip())
    if not stream:
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = json.dumps(data, ensure_ascii=False)[:500]
        yield content
        return
    # 手动按 UTF-8 解码 SSE 行：requests 的 decode_unicode 对未声明 charset 的
    # text/event-stream 会默认按 ISO-8859-1 解码，导致中文乱码
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("data:"):
            data = line[5:].strip()
        else:
            continue
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if obj.get("error"):
            raise RuntimeError(str(obj.get("error")))
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        tok = delta.get("content")
        if tok:
            yield tok


# ==================== 流式打印 + 命令/插件块提取 ====================
CMD_OPEN = "[[CMD]]"
CMD_CLOSE = "[[/CMD]]"


def stream_reply(tokens, extra_markers=None):
    """流式打印 AI 回复（隐藏 [[CMD]] 与插件标记块），返回 (可见文本, 块列表)。

    - 块列表中，命令块为纯字符串；插件调用块为 (插件名, 工具名, 内容) 元组
    - extra_markers: [(插件名, 工具名, 开标记, 闭标记, 说明), ...]
    """
    # marker 元组统一为 (种类, 开标记, 闭标记, 插件名, 工具名)
    markers = [("CMD", CMD_OPEN, CMD_CLOSE, None, None)]
    if extra_markers:
        markers += [("P", opn, clo, pname, name) for (pname, name, opn, clo, desc) in extra_markers]
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
    cur_kind = None      # "CMD" 或 "P"
    cur_open = None      # 当前所处块的开标记
    cur_close = None     # 当前块的闭标记
    cur_pname = None     # 插件名（仅插件块）
    cur_tname = None     # 工具名（仅插件块）
    block_buf = ""
    blocks = []
    full = []

    def flush_visible(s):
        if s:
            print(s, end="", flush=True)
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
                # 未出现完整开标记：若尾部是标记前缀，则保留等待更多 token
                held = 0
                for k in range(1, max(len(m) for m in all_marks)):
                    if is_prefix(pending[-k:]):
                        held = k
                if held:
                    if len(pending) > held:
                        flush_visible(pending[:-held])
                    pending = pending[-held:]
                    break  # 等下一个 token，避免 pending 不变导致死循环
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
                # 未出现完整闭标记：若尾部是标记前缀，则保留等待更多 token
                held = 0
                for k in range(1, len(cur_close)):
                    if cur_close.startswith(pending[-k:]):
                        held = k
                if held:
                    if len(pending) > held:
                        block_buf += pending[:-held]
                    pending = pending[-held:]
                    break  # 等下一个 token
                block_buf += pending
                pending = ""
    # 流结束后处理残留
    if cur_open is not None:
        block_buf += pending
        emit_block()  # 未闭合的块
    elif pending:
        flush_visible(pending)  # 残留文本（如未闭合的 "[[")
    print()
    return "".join(full), blocks


# ==================== 命令执行 ====================
def run_command(cmd, workspace, timeout=120):
    """在工作区目录执行命令，返回 (是否成功, 输出文本)。"""
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=workspace,
            capture_output=True, timeout=timeout,
        )
        out = (proc.stdout or b"") + (proc.stderr or b"")
        text = out.decode("utf-8", errors="replace")
        if "\ufffd" in text:  # UTF-8 解码出乱码，改用 GBK 重解（中文 Windows）
            text = out.decode("gbk", errors="replace")
        text = text.strip()
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n...(输出过长，已截断到 {MAX_OUTPUT_CHARS} 字符)"
        return proc.returncode == 0, text
    except subprocess.TimeoutExpired:
        return False, f"(命令执行超时，超过 {timeout} 秒已终止)"
    except Exception as e:
        return False, f"(命令执行失败: {e})"


# ==================== 插件处理器 ====================
# 轮换的浏览器头池，减少被目标网站反爬拒绝的概率
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
]
_UA_IDX = 0


def _next_ua():
    global _UA_IDX
    ua = _UA_POOL[_UA_IDX % len(_UA_POOL)]
    _UA_IDX += 1
    return ua


def _web_headers(referer=None):
    h = {
        "User-Agent": _next_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    if referer:
        h["Referer"] = referer
    return h


# 必应可用的搜索域名池：任一被反爬/被墙就换下一个
_BING_HOSTS = ["cn.bing.com", "www.bing.com", "global.bing.com", "bing.com"]


def _strip_html(html):
    """去掉 HTML 标签并压缩空白，用于正文提取。"""
    text = re.sub(
        r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>|<header.*?</footer>",
        " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&amp;|&lt;|&gt;|&quot;|&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---- 搜索缓存数据库 ----
def _search_db():
    """打开搜索缓存数据库（配置目录下 search_cache.db）。"""
    db_path = os.path.join(os.path.dirname(config_path()), "search_cache.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS search_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT NOT NULL,
        title TEXT,
        url TEXT,
        snippet TEXT,
        content TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    return conn


def _cache_search(query, results):
    """把搜索结果写入数据库。results: list of dict(title,url,snippet,content)"""
    try:
        conn = _search_db()
        for r in results:
            conn.execute(
                "INSERT INTO search_cache (query, title, url, snippet, content) VALUES (?,?,?,?,?)",
                (query, r.get("title", ""), r.get("url", ""),
                 r.get("snippet", ""), r.get("content", ""))
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _cache_lookup(query):
    """按 query 查缓存，返回 list of dict 或 None。"""
    try:
        conn = _search_db()
        cur = conn.execute(
            "SELECT title, url, snippet, content FROM search_cache WHERE query=? ORDER BY id DESC LIMIT 10",
            (query,))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        return [{"title": r[0], "url": r[1], "snippet": r[2], "content": r[3]} for r in rows]
    except Exception:
        return None


def _search_bing_cn(query, limit=5):
    """必应搜索：多域名逐个尝试，解析失败自动换下一个域名。返回 list of dict。"""
    last_err = ""
    for host in _BING_HOSTS:
        try:
            resp = http_session().get(
                f"https://{host}/search",
                params={"q": query, "count": limit, "mkt": "zh-CN", "setlang": "zh-hans"},
                headers=_web_headers(referer=f"https://{host}/"),
                timeout=(10, 20))
            if resp.status_code in (403, 429, 503):
                last_err = f"{host} 拒绝访问(HTTP {resp.status_code})"
                continue
            resp.raise_for_status()
            html = resp.text
            # 反爬验证页特征：没有 b_algo 且包含验证关键词 → 换域名
            items = re.findall(r'<li class="b_algo".*?</li>', html, flags=re.S)
            if not items and re.search(r"captcha|验证|请输入验证码|robot", html, flags=re.I):
                last_err = f"{host} 触发验证码"
                continue
            results = []
            for it in items[:limit]:
                m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', it, flags=re.S)
                if not m:
                    continue
                rurl = m.group(1)
                title = _strip_html(m.group(2))
                snip = ""
                sm = re.search(r'<p[^>]*>(.*?)</p>', it, flags=re.S)
                if sm:
                    snip = _strip_html(sm.group(1))[:300]
                results.append({"title": title, "url": rurl, "snippet": snip})
            if results:
                return results
            last_err = f"{host} 无结果"
        except Exception as e:
            last_err = f"{host} 请求失败: {e}"
            continue
    print(col(f"    [搜索失败] {last_err}", C.RED))
    return []


def _fetch_page_text(url, max_chars=3000):
    """抓取指定 URL 的正文文本。UA 池轮换 + Referer 伪装，失败返回错误说明。"""
    try:
        resp = http_session().get(url, headers=_web_headers(referer="https://www.bing.com/"),
                                  timeout=(10, 20), allow_redirects=True)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
        body = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.S | re.I)
        raw = body.group(1) if body else html
        text = _strip_html(raw)
        return text[:max_chars]
    except requests.exceptions.HTTPError as e:
        return f"(抓取失败: HTTP {e.response.status_code}，该网站可能拒绝访问或需要登录)"
    except Exception as e:
        return f"(抓取失败: {e})"


def handler_web_search(content, workspace=None):
    """联网搜索：必应，结果自动存数据库，命中缓存直接返回。"""
    query = content.strip().strip('"').strip("'")
    if not query:
        return False, "(搜索内容为空)"
    print(col(f"    [联网搜索] “{query}”", C.MAGENTA))
    cached = _cache_lookup(query)
    if cached:
        lines = ["(命中缓存)\n"]
        for i, r in enumerate(cached, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        return True, "\n".join(lines)
    results = _search_bing_cn(query)
    if not results:
        return False, "(必应未返回结果，可能被反爬或网络受限)"
    for r in results:
        r["content"] = ""
    _cache_search(query, results)
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    lines.append("\n(结果已存入数据库；如需深入阅读可输出 [[DSEARCH]]同关键词[[/DSEARCH]] 或 [[FETCH]]URL[[/FETCH]])")
    return True, "\n".join(lines)


def handler_web_search_deep(content, workspace=None):
    """深度联网搜索：必应 + 自动打开前 3 条结果网页抓正文。"""
    query = content.strip().strip('"').strip("'")
    if not query:
        return False, "(搜索内容为空)"
    print(col(f"    [深度搜索] “{query}”", C.MAGENTA))
    results = _search_bing_cn(query, limit=5)
    if not results:
        return False, "(必应未返回结果)"
    for i, r in enumerate(results[:3]):
        print(col(f"      ↳ 抓取网页 {i+1}/3: {r['url'][:70]}", C.DIM))
        r["content"] = _fetch_page_text(r["url"])
    _cache_search(query, results)
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"=== {i}. {r['title']} ===")
        lines.append(f"URL: {r['url']}")
        if r.get("content") and not r["content"].startswith("(抓取失败"):
            lines.append(f"正文摘要:\n{r['content']}")
        else:
            lines.append(f"摘要: {r['snippet']}")
        lines.append("")
    lines.append("(深度结果已存入数据库)")
    return True, "\n".join(lines)


def handler_fetch_page(content, workspace=None):
    """抓取指定 URL 的网页正文。"""
    url = content.strip()
    if not url.startswith(("http://", "https://")):
        return False, f"(URL 必须以 http:// 或 https:// 开头，当前: {url[:80]})"
    print(col(f"    [抓取网页] {url[:80]}", C.MAGENTA))
    text = _fetch_page_text(url, max_chars=5000)
    _cache_search("fetch:" + url, [{"title": url, "url": url, "snippet": "", "content": text}])
    return True, f"URL: {url}\n\n正文内容:\n{text}"


def handler_make_plugin(content, workspace=None):
    """插件工坊处理器：校验 AI 写的插件 JSON，合法则自动安装到插件目录。"""
    text = content.strip()
    # 去掉可能包裹的代码块围栏
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception as e:
        return False, f"(插件 JSON 解析失败: {e}。请检查格式是否符合插件规范)"
    ok, err = validate_plugin(data)
    if not ok:
        return False, f"(插件校验未通过: {err}。请参考插件 JSON 格式规范重写)"
    pid = data["id"]
    name = data["name"]
    fn = pid + ".json"
    path = os.path.join(plugins_dir(), fn)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return False, f"(插件保存失败: {e})"
    return True, f"插件“{name}”(id: {pid}) 已自动安装到插件目录 {path}，立即生效，可直接调用其工具。"


# 注册处理器
PLUGIN_HANDLERS["web_search"] = handler_web_search
PLUGIN_HANDLERS["web_search_deep"] = handler_web_search_deep
PLUGIN_HANDLERS["fetch_page"] = handler_fetch_page
PLUGIN_HANDLERS["make_plugin"] = handler_make_plugin

# ---- 3.0 内置工作区/浏览器/容器工具 ----
try:
    from workspace_tools import (
        handler_file_read, handler_file_write, handler_dir_list,
        handler_git_status, handler_git_diff, handler_git_commit,
        handler_browser_open, handler_browser_read,
    )
    PLUGIN_HANDLERS["file_read"] = handler_file_read
    PLUGIN_HANDLERS["file_write"] = handler_file_write
    PLUGIN_HANDLERS["dir_list"] = handler_dir_list
    PLUGIN_HANDLERS["git_status"] = handler_git_status
    PLUGIN_HANDLERS["git_diff"] = handler_git_diff
    PLUGIN_HANDLERS["git_commit"] = handler_git_commit
    PLUGIN_HANDLERS["browser_open"] = handler_browser_open
    PLUGIN_HANDLERS["browser_read"] = handler_browser_read
except Exception as _e:
    print(col(f"[警告] 工作区工具加载失败: {_e}", C.YELLOW))

try:
    import container as _container_mod

    def handler_container_exec(content, workspace=None):
        """在容器里执行命令（Windows=WSL 发行版，Linux=Docker 镜像）。"""
        cfg = Config()
        settings = cfg.data["settings"]
        mode = settings.get("container_mode", "off")
        if mode != "container":
            return False, "容器模式未开启，请先在设置里选择容器模式并配置目标。"
        if _container_mod.is_windows():
            target = settings.get("container_target") or ""
            if not target:
                return False, "未选择 WSL 发行版，请在设置里选择。"
            return _container_mod.wsl_exec(target, content, workspace=workspace)
        image = settings.get("container_target") or settings.get("container_image") or "ubuntu:latest"
        return _container_mod.docker_exec(image, content, workspace=workspace)

    PLUGIN_HANDLERS["container_exec"] = handler_container_exec
except Exception as _e:
    print(col(f"[警告] 容器工具加载失败: {_e}", C.YELLOW))


def ask(prompt):
    """读取用户输入：去空白、去 BOM（兼容重定向自带 BOM 的输入文件）。"""
    try:
        return input(prompt).strip().lstrip("\ufeff")
    except (EOFError, KeyboardInterrupt):
        raise


def ask_confirm(cmd):
    """按需模式下询问用户是否允许执行命令。"""
    while True:
        ans = ask(col(f"  允许 AI 执行此命令吗？ [y/n]: ", C.YELLOW)).lower()
        if ans in ("y", "yes", "是", "1"):
            return True
        if ans in ("n", "no", "否", "0"):
            return False
        print(col("  请输入 y 或 n。", C.DIM))


# ==================== 文件夹选择 ====================
def pick_folder():
    """弹出系统文件夹选择窗口（tkinter）。失败返回 None。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="选择工作区目录")
        root.destroy()
        return folder if folder else None
    except Exception:
        return None


def choose_workspace(current):
    """选择工作区：支持窗口选择或手动输入路径。返回新路径或 None。"""
    print(col("  1. 弹出文件夹选择窗口", C.CYAN))
    print(col("  2. 手动输入目录路径", C.CYAN))
    print(col("  0. 取消", C.CYAN))
    choice = ask("  请选择: ").strip()
    if choice == "1":
        folder = pick_folder()
        if folder:
            return folder
        print(col("  未选择任何目录。", C.YELLOW))
        return None
    if choice == "2":
        path = ask("  请输入目录路径: ").strip().strip('"')
        if not path:
            return None
        path = os.path.abspath(os.path.expanduser(path))
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception as e:
            print(col(f"  目录无效: {e}", C.RED))
            return None
    return None


# ==================== 对话 ====================
BUILTIN_TOOLS = [
    ("file_read", "读取工作区内文件，内容=文件路径。", "[[FILEREAD]]", "[[/FILEREAD]]"),
    ("file_write", "写入工作区内文件，内容=第一行文件路径，换行后是文件内容。", "[[FILEWRITE]]", "[[/FILEWRITE]]"),
    ("dir_list", "列出目录内容，内容=目录路径（默认当前工作区）。", "[[DIRLIST]]", "[[/DIRLIST]]"),
    ("git_status", "查看 git 工作区状态，内容可留空。", "[[GITSTATUS]]", "[[/GITSTATUS]]"),
    ("git_diff", "查看 git 改动，内容=可选文件路径。", "[[GITDIFF]]", "[[/GITDIFF]]"),
    ("git_commit", "提交 git 改动，内容=提交信息。", "[[GITCOMMIT]]", "[[/GITCOMMIT]]"),
    ("browser_open", "内置浏览器打开网页并返回正文，内容=URL。", "[[BROWSE]]", "[[/BROWSE]]"),
    ("container_exec", "在容器里执行命令（Windows=WSL / Linux=Docker），内容=命令。", "[[CTREXEC]]", "[[/CTREXEC]]"),
]

_mcp_markers_cache = {"ts": 0.0, "val": []}


def builtin_tool_markers(settings):
    """按设置返回启用的内置工具标记列表。"""
    markers = []
    if settings.get("workspace_tools", True):
        for tname, desc, opn, clo in BUILTIN_TOOLS[:6]:
            markers.append(("内置工具", tname, opn, clo, desc))
    if settings.get("browser_enabled", True):
        markers.append(("内置工具", "browser_open", "[[BROWSE]]", "[[/BROWSE]]",
                        "内置浏览器打开网页并返回正文，内容=URL。"))
    if settings.get("container_mode") == "container":
        markers.append(("内置工具", "container_exec", "[[CTREXEC]]", "[[/CTREXEC]]",
                        "在容器里执行命令（Windows=WSL / Linux=Docker），内容=命令。"))
    # MCP 工具
    try:
        import mcp_client
        servers = settings.get("mcp_servers") or {}
        if servers:
            now = time.time()
            if now - _mcp_markers_cache["ts"] > 30:
                _mcp_markers_cache["val"] = mcp_client.all_markers(settings)
                _mcp_markers_cache["ts"] = now
            markers += _mcp_markers_cache["val"]
    except Exception:
        pass
    return markers


def _estimate_tokens(messages):
    """粗略估算 token 数（中文约 1 字≈1.5 token，按字符数/1.8 估算）。"""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += max(1, int(len(c) / 1.8))
    return total


def _compress_history(history, max_tokens):
    """上下文超限时压缩：保留首条（含开场信息）+ 最近的若干条。"""
    if not history:
        return history, False
    kept = [history[0]]
    rest = history[1:]
    # 从后往前收集，直到逼近预算（留 30% 给新消息和 system）
    budget = int(max_tokens * 0.7)
    tail = []
    used = _estimate_tokens(kept)
    for m in reversed(rest):
        cost = _estimate_tokens([m])
        if used + cost > budget and tail:
            break
        tail.insert(0, m)
        used += cost
    compressed = kept + tail
    return compressed, len(compressed) < len(history)


def build_messages(cfg, model_id):
    """构造消息列表：系统提示词 + 插件/内置工具说明 + 记忆 + 历史(自动压缩)。"""
    settings = cfg.data["settings"]
    system = settings["system_prompt"]

    lines = [system]
    tool_lines = []

    markers = enabled_plugin_markers()
    if markers:
        tool_lines.append("## 可用工具（由已安装插件提供）")
        tool_lines.append("需要真实信息或执行插件功能时，在回复中输出对应的开标记+内容+闭标记，程序会执行并把结果回传给你。")
        for pname, tname, opn, clo, desc in markers:
            tool_lines.append(f"- [{tname}] {desc}  (标记: {opn}内容{clo})")

    bmarkers = builtin_tool_markers(settings)
    if bmarkers:
        tool_lines.append("## 内置工具（工作区/浏览器/容器/MCP）")
        tool_lines.append("这些工具同样通过标记调用，程序执行后会把结果回传。")
        for pname, tname, opn, clo, desc in bmarkers:
            tool_lines.append(f"- [{tname}] {desc}  (标记: {opn}内容{clo})")

    if tool_lines:
        lines.append("")
        lines.extend(tool_lines)
        lines.append("")
        lines.append(PLUGIN_FORMAT_DOC)

    # 跨会话记忆注入
    if settings.get("memory_enabled", True):
        try:
            import memory as _memory
            mems = _memory.load_memories()
            if mems:
                mem_lines = ["## 关于用户的长期记忆（上次会话沉淀，供参考）"]
                for mm in mems[:10]:
                    mem_lines.append(f"- [{mm['tag']}] {mm['content'][:300]}")
                lines.append("")
                lines.append("\n".join(mem_lines))
        except Exception:
            pass

    messages = [{"role": "system", "content": "\n".join(lines)}]

    history = cfg.history(model_id)[-HISTORY_LIMIT:]
    if settings.get("context_auto_compress", True):
        max_tok = int(settings.get("context_max_tokens", 24000) or 24000)
        history, compressed = _compress_history(history, max_tok)
        if compressed:
            messages.append({"role": "system",
                             "content": "（较早的对话因上下文限制已自动精简，仅保留近期内容；如需要更早信息请直接询问用户。）"})
    messages += history
    return messages


def plugin_markers_for_request():
    """供 stream_reply 使用的插件标记列表: [(插件名, 工具名, 开, 闭, 说明)]
    含插件 + 内置工具（工作区/浏览器/容器）+ MCP 工具。"""
    try:
        settings = Config().data["settings"]
    except Exception:
        settings = dict(DEFAULT_SETTINGS)
    return enabled_plugin_markers() + builtin_tool_markers(settings)


class _Spinner:
    """请求期间在控制台打印等待点，首个 token 到达即停止（避免以为程序卡死）。"""

    def __init__(self, text="等待服务器响应"):
        self.text = text
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        print(col(self.text, C.DIM), end="", flush=True)
        while not self._stop.wait(0.5):
            print(col(".", C.DIM), end="", flush=True)
        print()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1)


def _friendly_error(e):
    import requests
    if isinstance(e, requests.exceptions.Timeout):
        return "请求超时：服务器长时间无响应（检查网络，或稍后再试）"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "连接失败：无法到达服务器（检查 API 地址、网络是否可用）"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP 错误: {e}"
    if isinstance(e, RuntimeError):
        return str(e)
    return f"请求异常: {e}"


def chat_once(cfg, provider, model, messages):
    """调用一次 API 并流式打印，返回 (可见回复文本, 块列表)；失败返回 (None, None)。"""
    settings = cfg.data["settings"]
    markers = plugin_markers_for_request()
    try:
        retries = max(0, int(settings.get("api_retries", 1)))
    except Exception:
        retries = 1
    attempts = list(range(1 + retries))
    for ai, attempt in enumerate(attempts):
        for stream in (True, False):
            spinner = _Spinner("等待服务器响应")
            spinner.start()
            try:
                tokens = chat_request(provider, model["name"], messages, settings, stream=stream)

                def wrapped():
                    first = True
                    for tok in tokens:
                        if first:
                            spinner.stop()
                            first = False
                        yield tok

                result = stream_reply(wrapped(), extra_markers=markers)
                return result
            except Exception as e:
                spinner.stop()
                tag = C.YELLOW if (ai + stream * 0.5) == 0 else C.RED
                print(col(f"\n[{_friendly_error(e)}]", tag))
                if stream:
                    print(col("正在改用非流式方式重试...", C.DIM))
            finally:
                spinner.stop()
        if attempt < retries:
            print(col(f"(第 {attempt + 1} 次重试...)", C.DIM))
            time.sleep(1)
    return None, None


def chat_loop(cfg, model):
    """与选定模型对话。支持工作区命令执行。"""
    provider = cfg.provider(model.get("provider_id"))
    if not provider:
        print(col("[错误] 模型对应的供应商不存在，请重新添加。", C.RED))
        return

    settings = cfg.data["settings"]
    workspace = settings["workspace"]
    mode = settings["access_mode"]
    mode_text = "完全访问（命令自动执行）" if mode == "full" else "按需访问（命令先询问）"

    messages = build_messages(cfg, model["id"])
    history = messages[1:]  # 不含 system

    clear_screen()
    print(col("=" * 60, C.BLUE))
    print(col(f"  对话模型   : {model['display']}", C.GREEN))
    print(col(f"  工作区     : {workspace}", C.GREEN))
    print(col(f"  访问模式   : {mode_text}", C.GREEN))
    print(col("  输入 /help 查看对话指令", C.DIM))
    print(col("=" * 60, C.BLUE))

    # 继续上次对话时，把最近几轮历史打印出来，方便接着聊
    if history:
        print(col("----- 最近对话 -----", C.DIM))
        for m in history[-6:]:
            role = "你" if m["role"] == "user" else "AI"
            color = C.GREEN if m["role"] == "user" else C.CYAN
            text = m["content"]
            # 命令块执行结果这类内部消息，只显示一行摘要
            if text.startswith("[命令执行结果]"):
                text = "(已执行命令并取回结果)"
            elif text.startswith("[插件执行结果]"):
                text = "(已调用插件并取回结果)"
            elif text.startswith("[插件调用失败]") or text.startswith("用户取消了命令执行"):
                text = "(跳过)"
            print(col(f"{role}> ", color) + text)
        print(col("----- 历史结束 -----", C.DIM))

    while True:
        try:
            raw = ask(col("你 > ", C.GREEN)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue

        # ---- 整轮处理加异常兜底：任何一步出错都只提示并继续，不让程序崩溃 ----
        try:
            # ---- 对话指令 ----
            if raw == "/exit":
                break
            if raw == "/new":
                messages = build_messages(cfg, model["id"])
                history = messages[1:]
                cfg.data["histories"][model["id"]] = []
                cfg.save()
                print(col("(已开启新对话，历史已清空)", C.DIM))
                continue
            if raw == "/clear":
                clear_screen()
                print(col(f"模型: {model['display']} | 工作区: {workspace}", C.BLUE))
                continue
            if raw == "/workspace":
                new_ws = choose_workspace(workspace)
                if new_ws:
                    workspace = new_ws
                    settings["workspace"] = new_ws
                    cfg.save()
                    print(col(f"(工作区已切换: {workspace})", C.GREEN))
                continue
            if raw == "/model":
                print(col("(已保存当前对话，返回模型列表重新选择)", C.DIM))
                cfg.save()
                return
            if raw == "/help":
                print(col("""对话指令:
  /exit      退出对话，返回模型列表
  /new       开启新对话（清空当前模型历史）
  /clear     清屏
  /workspace 切换工作区
  /model     返回模型列表重新选择
  /help      显示本帮助""", C.CYAN))
                continue

            messages.append({"role": "user", "content": raw})
            history.append(messages[-1])

            steps = 0
            while steps < MAX_AGENT_STEPS:
                steps += 1
                print(col("AI > ", C.CYAN), end="")
                reply, blocks = chat_once(cfg, provider, model, messages)
                if reply is None:
                    break
                messages.append({"role": "assistant", "content": reply})
                history.append(messages[-1])

                if not blocks:
                    break

                # 执行命令块与插件调用
                ran_any = False
                for blk in blocks:
                    if isinstance(blk, str):
                        # ---- 命令块 ----
                        cmd = blk
                        print(col(f"\n→ 执行命令: {cmd}", C.MAGENTA))
                        should_run = True
                        if mode == "on_demand":
                            should_run = ask_confirm(cmd)
                        if not should_run:
                            messages.append({"role": "user", "content":
                                             f"用户取消了命令执行：{cmd}，请改用其他方式或询问用户。"})
                            history.append(messages[-1])
                            print(col("(已跳过该命令)", C.DIM))
                            continue
                        if mode == "full":
                            print(col("(完全访问模式，自动执行)", C.DIM))
                        ok, out = run_command(cmd, workspace)
                        status = "成功" if ok else "失败"
                        print(col(f"  [{status}] 输出:", C.DIM))
                        if out:
                            print(col(out, C.DIM))
                        else:
                            print(col("  (无输出)", C.DIM))
                        result_note = f"[命令执行结果]\n命令: {cmd}\n状态: {status}\n输出:\n{out}"
                        messages.append({"role": "user", "content": result_note})
                        history.append(messages[-1])
                        ran_any = True
                    else:
                        # ---- 插件调用块: (插件名, 工具名, 内容) ----
                        pname, tname, content = blk
                        print(col(f"\n→ 调用插件 [{pname}] {tname}", C.MAGENTA))
                        handler_fn = None
                        for p in load_plugins():
                            if p["name"] == pname:
                                for t in p.get("tools", []):
                                    if t.get("name") == tname:
                                        handler_fn = PLUGIN_HANDLERS.get(t.get("handler"))
                                        break
                                break
                        if handler_fn is None:
                            messages.append({"role": "user", "content":
                                             f"[插件调用失败] 插件 {pname} 的工具 {tname} 不存在或已卸载，请勿再调用。"})
                            history.append(messages[-1])
                            print(col("  (插件不存在，已跳过)", C.YELLOW))
                            continue
                        try:
                            ok, out = handler_fn(content, workspace)
                        except Exception as e:
                            ok, out = False, f"(插件执行异常: {e})"
                        status = "成功" if ok else "失败"
                        print(col(f"  [{status}]", C.DIM))
                        if out:
                            print(col(out, C.DIM))
                        result_note = f"[插件执行结果]\n插件: {pname}\n工具: {tname}\n状态: {status}\n结果:\n{out}"
                        messages.append({"role": "user", "content": result_note})
                        history.append(messages[-1])
                        ran_any = True

                if not ran_any:
                    break
                print(col("— AI 正在根据执行结果继续 —", C.DIM))

            if steps >= MAX_AGENT_STEPS:
                print(col(f"(已达单次连续执行上限 {MAX_AGENT_STEPS} 轮，如需要请继续提问)", C.DIM))

            # 保存历史（截断）
            cfg.data["histories"][model["id"]] = history[-HISTORY_LIMIT:]
            cfg.save()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        except Exception as e:
            print(col(f"\n[对话发生错误] {e}（已保留上下文，可继续提问）", C.RED))
            try:
                cfg.data["histories"][model["id"]] = history[-HISTORY_LIMIT:]
                cfg.save()
            except Exception:
                pass

        print(col("-" * 60, C.BLUE))


# ==================== 主菜单 ====================
def main_menu(cfg):
    s = cfg.data["settings"]
    mode_text = "完全访问" if s["access_mode"] == "full" else "按需访问"
    print()
    print(col("=" * 44, C.BLUE))
    print(col(f"  {APP_NAME} v{APP_VERSION}", C.BOLD))
    print(col(f"  工作区   : {s['workspace']}", C.GREEN))
    print(col(f"  访问模式 : {mode_text}", C.GREEN))
    print(col("=" * 44, C.BLUE))
    print(col(" 1. 连接已有模型", C.CYAN))
    print(col(" 2. 添加供应商", C.CYAN))
    print(col(" 3. 设置", C.CYAN))
    print(col(" 4. 管理插件", C.CYAN))
    print(col(" 0. 退出", C.CYAN))


def connect_menu(cfg):
    """选项 1：连接已有模型进行对话。"""
    models = cfg.data["models"]
    if not models:
        clear_screen()
        print(col("\n[提示] 还没有任何模型，请先选择“2. 添加供应商”。", C.YELLOW))
        return
    while True:
        clear_screen()
        print(col("========== 选择模型 ==========", C.BLUE))
        for i, m in enumerate(models, 1):
            n = len(cfg.history(m["id"]))
            tag = f"  (历史 {n} 条)" if n else ""
            print(col(f" {i}. {m['display']}", C.CYAN) + col(tag, C.DIM))
        print(col(" 0. 返回主菜单", C.CYAN))
        try:
            choice = ask("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(models)):
            print(col("无效选择。", C.YELLOW))
            continue
        model = models[int(choice) - 1]

        # 继续上次对话 / 新建对话
        n = len(cfg.history(model["id"]))
        print()
        if n:
            print(col("========== 对话历史 ==========", C.BLUE))
            print(col(f" 1. 继续上次对话（{n} 条记录）", C.CYAN))
            print(col(" 2. 新建对话", C.CYAN))
            his_choice = ask("请选择: ").strip()
            if his_choice == "2":
                cfg.data["histories"][model["id"]] = []
                cfg.save()
                print(col("(已开启全新对话)", C.DIM))
        else:
            print(col("(首次对话，已开启全新会话)", C.DIM))

        # 工作区选择
        print()
        print(col("========== 工作区 ==========", C.BLUE))
        print(col(f" 1. 使用当前工作区: {cfg.data['settings']['workspace']}", C.CYAN))
        print(col(" 2. 选择其他工作区", C.CYAN))
        ws_choice = ask("请选择: ").strip()
        if ws_choice == "2":
            new_ws = choose_workspace(cfg.data["settings"]["workspace"])
            if new_ws:
                cfg.data["settings"]["workspace"] = new_ws
                cfg.save()
        chat_loop(cfg, model)


def add_provider_menu(cfg):
    """选项 2：添加供应商（内置 + 自定义）并拉取模型。"""
    existing_ids = {p["id"] for p in cfg.data["providers"]}
    while True:
        clear_screen()
        print(col("========== 添加供应商 ==========", C.BLUE))
        options = []
        for i, p in enumerate(DEFAULT_PROVIDERS, 1):
            tag = " (已添加)" if p["id"] in existing_ids else ""
            print(col(f" {i}. {p['name']}  [{p['base_url']}]{tag}", C.CYAN))
            options.append(p)
        print(col(f" {len(options) + 1}. 自定义供应商", C.MAGENTA))
        print(col(" 0. 返回主菜单", C.CYAN))
        try:
            choice = ask("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            provider = dict(options[int(choice) - 1])
            provider["full_endpoint"] = False
            existing = cfg.provider(provider["id"])
            if existing:
                key_hint = existing.get("api_key") or ""
                key = ask(col(f"该供应商已存在（Key: {key_hint[:6] or '(空)'}...），"
                                f"请输入新的 API Key（回车保留原值）: ", C.YELLOW)).strip()
                if key:
                    existing["api_key"] = key
                    cfg.save()
                provider = existing
            else:
                key = ask(col("请输入 API Key（无鉴权的本地服务可直接回车）: ", C.YELLOW)).strip()
                provider["api_key"] = key
                cfg.data["providers"].append(provider)
                existing_ids.add(provider["id"])
                cfg.save()
            add_models_menu(cfg, provider)
        elif choice == str(len(options) + 1):
            add_custom_provider(cfg, existing_ids)
        else:
            print(col("无效选择。", C.YELLOW))


def add_custom_provider(cfg, existing_ids):
    """自定义供应商：名称 + API 地址 + 是否完整地址 + API Key。"""
    print(col("--- 自定义供应商 ---", C.MAGENTA))
    name = ask(col("供应商名称: ", C.YELLOW)).strip()
    if not name:
        print(col("名称不能为空。", C.YELLOW))
        return
    url = ask(col("API 地址: ", C.YELLOW)).strip()
    if not url:
        print(col("地址不能为空。", C.YELLOW))
        return
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    full = ask(col("该地址是否已是完整接口地址（含 /chat/completions）？[y/n]: ", C.YELLOW)).strip().lower()
    full_endpoint = full in ("y", "yes", "是", "1")
    key = ask(col("API Key（无鉴权可留空）: ", C.YELLOW)).strip()

    provider = {
        "id": "custom-" + uuid.uuid4().hex[:8],
        "name": name,
        "base_url": url,
        "api_key": key,
        "full_endpoint": full_endpoint,
    }
    cfg.data["providers"].append(provider)
    cfg.save()
    print(col(f"(供应商“{name}”已添加)", C.GREEN))
    add_models_menu(cfg, provider)


# 常用模型兜底：拉取列表为空/不全时，提示用户可直接输入的常见模型名
KNOWN_MODELS = {
    "zhipu": ["glm-4.5", "glm-4.5-flash", "glm-4-plus", "glm-4-flash", "glm-4-long",
              "glm-4-air", "glm-4v-plus", "glm-4v-flash"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "qwen": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"],
    "kimi": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
                    "Qwen/Qwen2.5-72B-Instruct", "THUDM/glm-4-9b-chat"],
}


def add_models_menu(cfg, provider):
    """拉取供应商模型列表，让用户选择要添加的模型。

    支持混合输入：序号（多个用逗号分隔）、all（全选）、或直接输入模型名称
    （列表里没有的也可以手动补充，如“1,3,glm-4-long,my-model”）。
    """
    print()
    print(col(f"--- 拉取“{provider['name']}”的模型列表 ---", C.MAGENTA))
    ids = []
    try:
        ids = fetch_models(provider, timeout=cfg.data["settings"].get("connect_timeout", 15))
    except Exception as e:
        print(col(f"[无法拉取模型列表: {e}]", C.YELLOW))

    # 已添加过的模型（避免重复添加）
    existing = {m["name"] for m in cfg.data["models"] if m["provider_id"] == provider["id"]}

    if ids:
        print(col(f"共获取 {len(ids)} 个模型（列表不全？可直接输入模型名称补充添加）：", C.CYAN))
        for i, mid in enumerate(ids, 1):
            mark = " (已添加)" if mid in existing else ""
            print(col(f" {i}. {mid}", C.CYAN) + col(mark, C.DIM))
        sel = ask(col("输入序号（逗号分隔）/ all 全选 / 或直接输入模型名称: ", C.YELLOW)).strip()
    else:
        known = KNOWN_MODELS.get(provider["id"]) or KNOWN_MODELS.get(provider.get("name", "").lower())
        hint = ""
        if known:
            hint = f"（常用模型参考: {', '.join(known[:6])}）"
        print(col(f"[列表为空] 请手动输入模型名称，多个用逗号分隔 {hint}", C.YELLOW))
        sel = ask(col("输入模型名称: ", C.YELLOW)).strip()

    picked = []
    if sel.lower() in ("all", "*"):
        picked = list(ids)
    else:
        for part in sel.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if part.isdigit() and 1 <= int(part) <= len(ids):
                picked.append(ids[int(part) - 1])
            else:
                # 直接作为模型名称添加（允许手动补充列表外的模型）
                picked.append(part)

    # 去重（名称相同不重复添加）
    seen = set()
    picked2 = []
    for mid in picked:
        if mid not in seen and mid not in existing:
            seen.add(mid)
            picked2.append(mid)
    picked = picked2

    added = 0
    for mid in picked:
        display = f"{provider['name']} - {mid}"
        cfg.data["models"].append({
            "id": "m-" + uuid.uuid4().hex[:8],
            "provider_id": provider["id"],
            "name": mid,
            "display": display,
        })
        added += 1
        print(col(f"  ✓ 已添加模型: {display}", C.GREEN))
    if added:
        cfg.save()
        print(col(f"(本次共添加 {added} 个模型)", C.GREEN))
    else:
        print(col("(没有新增模型：输入为空，或所选模型均已添加过)", C.DIM))
    print(col("(如需继续添加其他模型，可再次进入“添加供应商”。)", C.DIM))


# ==================== 设置 ====================
def settings_menu(cfg):
    while True:
        s = cfg.data["settings"]
        mode_text = "完全访问" if s["access_mode"] == "full" else "按需访问"
        clear_screen()
        print(col("========== 设置 ==========", C.BLUE))
        effort_text = {"off": "不启用", "low": "低", "medium": "中", "high": "高"}.get(s.get("reasoning_effort", "off"), s.get("reasoning_effort", "off"))
        proxy_text = "开启（读系统代理）" if s.get("use_proxy") else "关闭（直连，推荐）"
        print(col(f" 1. AI 访问模式       : {mode_text}", C.CYAN))
        print(col(f" 2. 温度 (temperature): {s['temperature']}", C.CYAN))
        print(col(f" 3. 最大回复 tokens   : {s['max_tokens']}", C.CYAN))
        print(col(f" 4. 系统提示词         : (当前 {len(s['system_prompt'])} 字)", C.CYAN))
        print(col(f" 5. 默认工作区         : {s['workspace']}", C.CYAN))
        print(col(f" 6. 思考强度           : {effort_text}", C.CYAN))
        print(col(f" 7. Top-P (核采样)    : {s.get('top_p', 1.0)}", C.CYAN))
        print(col(f" 8. 连接超时(秒)      : {s.get('connect_timeout', 15)}", C.CYAN))
        print(col(f" 9. API 重试次数      : {s.get('api_retries', 1)}", C.CYAN))
        print(col(f"10. 使用系统代理      : {proxy_text}", C.CYAN))
        print(col("11. 管理供应商和模型", C.CYAN))
        print(col("12. 清除对话历史", C.CYAN))
        print(col(" 0. 返回主菜单", C.CYAN))
        try:
            choice = ask("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice == "1":
            print(col("完全访问: AI 执行命令无需确认，可自行操作工作区（谨慎）。", C.YELLOW))
            print(col("按需访问: AI 每次要执行命令时都会先询问你。", C.YELLOW))
            new_mode = ask(col(f"选择模式 [{s['access_mode']}] (full/on_demand): ", C.YELLOW)).strip()
            if new_mode in ("full", "on_demand"):
                s["access_mode"] = new_mode
                cfg.save()
                print(col("已保存。", C.GREEN))
            else:
                print(col("无效输入。", C.YELLOW))
        elif choice == "2":
            try:
                val = float(ask(col("温度 (0.0~2.0): ", C.YELLOW)).strip())
                if 0.0 <= val <= 2.0:
                    s["temperature"] = val
                    cfg.save()
                    print(col("已保存。", C.GREEN))
                else:
                    print(col("超出范围。", C.YELLOW))
            except ValueError:
                print(col("无效数字。", C.YELLOW))
        elif choice == "3":
            try:
                val = int(ask(col("最大回复 tokens (正整数): ", C.YELLOW)).strip())
                if val > 0:
                    s["max_tokens"] = val
                    cfg.save()
                    print(col("已保存。", C.GREEN))
                else:
                    print(col("无效数字。", C.YELLOW))
            except ValueError:
                print(col("无效数字。", C.YELLOW))
        elif choice == "4":
            print(col("当前系统提示词:", C.CYAN))
            print(col(s["system_prompt"], C.DIM))
            print(col("输入新的系统提示词（输入 . 保持不变，输入 /d 恢复默认）:", C.CYAN))
            new_prompt = ask("> ").strip()
            if new_prompt == ".":
                continue
            if new_prompt == "/d":
                s["system_prompt"] = DEFAULT_SYSTEM_PROMPT
            elif new_prompt:
                s["system_prompt"] = new_prompt
            cfg.save()
            print(col("已保存。", C.GREEN))
        elif choice == "5":
            new_ws = choose_workspace(s["workspace"])
            if new_ws:
                s["workspace"] = new_ws
                cfg.save()
                print(col(f"默认工作区已更新: {new_ws}", C.GREEN))
        elif choice == "6":
            print(col("思考强度（仅支持该参数的模型生效，如部分 o 系列/DeepSeek R1）:", C.YELLOW))
            print(col("  off=不发送(兼容所有模型)  low=低  medium=中  high=高", C.YELLOW))
            val = ask(col(f"输入新值 [{s.get('reasoning_effort','off')}]: ", C.YELLOW)).strip().lower()
            if val in ("off", "low", "medium", "high"):
                s["reasoning_effort"] = val
                cfg.save()
                print(col("已保存。", C.GREEN))
            else:
                print(col("无效输入（off/low/medium/high）。", C.YELLOW))
        elif choice == "7":
            try:
                val = float(ask(col("Top-P (0.0~1.0): ", C.YELLOW)).strip())
                if 0.0 < val <= 1.0:
                    s["top_p"] = val
                    cfg.save()
                    print(col("已保存。", C.GREEN))
                else:
                    print(col("超出范围。", C.YELLOW))
            except ValueError:
                print(col("无效数字。", C.YELLOW))
        elif choice == "8":
            try:
                val = int(ask(col("连接超时（秒，3~60）: ", C.YELLOW)).strip())
                if 3 <= val <= 60:
                    s["connect_timeout"] = val
                    cfg.save()
                    print(col("已保存。", C.GREEN))
                else:
                    print(col("超出范围。", C.YELLOW))
            except ValueError:
                print(col("无效数字。", C.YELLOW))
        elif choice == "9":
            try:
                val = int(ask(col("API 失败重试次数（0~5）: ", C.YELLOW)).strip())
                if 0 <= val <= 5:
                    s["api_retries"] = val
                    cfg.save()
                    print(col("已保存。", C.GREEN))
                else:
                    print(col("超出范围。", C.YELLOW))
            except ValueError:
                print(col("无效数字。", C.YELLOW))
        elif choice == "10":
            cur = s.get("use_proxy", False)
            print(col("使用系统代理：若你的代理软件设置残留导致连不上，请保持关闭（直连）。", C.YELLOW))
            val = ask(col(f"开启使用系统代理？[y/n] (当前: {'开' if cur else '关'}): ", C.YELLOW)).strip().lower()
            if val in ("y", "yes", "是", "1"):
                s["use_proxy"] = True
            elif val in ("n", "no", "否", "0"):
                s["use_proxy"] = False
            else:
                print(col("无效输入。", C.YELLOW))
                continue
            cfg.save()
            print(col("已保存。", C.GREEN))
        elif choice == "11":
            manage_menu(cfg)
        elif choice == "12":
            clear_history_menu(cfg)
        else:
            print(col("无效选择。", C.YELLOW))


def manage_menu(cfg):
    while True:
        print()
        print(col("========== 管理供应商和模型 ==========", C.BLUE))
        print(col(" 1. 查看供应商列表", C.CYAN))
        print(col(" 2. 删除供应商（连带其模型）", C.CYAN))
        print(col(" 3. 查看模型列表", C.CYAN))
        print(col(" 4. 删除模型", C.CYAN))
        print(col(" 5. 添加模型（拉取列表或手动输入）", C.CYAN))
        print(col(" 0. 返回", C.CYAN))
        try:
            choice = ask("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice == "5":
            providers = cfg.data["providers"]
            if not providers:
                print(col("(暂无供应商，请先添加供应商)", C.DIM))
                continue
            for i, p in enumerate(providers, 1):
                print(col(f" {i}. {p['name']}", C.CYAN))
            sel = ask(col("选择供应商序号: ", C.YELLOW)).strip()
            if not sel.isdigit() or not (1 <= int(sel) <= len(providers)):
                print(col("无效选择。", C.YELLOW))
                continue
            add_models_menu(cfg, providers[int(sel) - 1])
            continue
        if choice == "1":
            providers = cfg.data["providers"]
            if not providers:
                print(col("(暂无供应商)", C.DIM))
                continue
            for i, p in enumerate(providers, 1):
                key = (p.get("api_key") or "")
                key_disp = (key[:6] + "...") if key else "(空)"
                print(col(f" {i}. {p['name']}  [{p['base_url']}]  Key: {key_disp}", C.CYAN))
        elif choice == "2":
            providers = cfg.data["providers"]
            if not providers:
                print(col("(暂无供应商)", C.DIM))
                continue
            for i, p in enumerate(providers, 1):
                print(col(f" {i}. {p['name']}", C.CYAN))
            sel = ask(col("输入要删除的供应商序号: ", C.YELLOW)).strip()
            if not sel.isdigit() or not (1 <= int(sel) <= len(providers)):
                print(col("无效选择。", C.YELLOW))
                continue
            p = providers[int(sel) - 1]
            if ask(col(f"确认删除供应商“{p['name']}”及其全部模型？[y/n]: ", C.RED)).strip().lower() in ("y", "yes"):
                cfg.data["providers"].remove(p)
                cfg.data["models"] = [m for m in cfg.data["models"] if m["provider_id"] != p["id"]]
                for mid in [m["id"] for m in cfg.data["models"]]:
                    cfg.data["histories"].pop(mid, None)
                cfg.save()
                print(col(f"已删除“{p['name']}”。", C.GREEN))
        elif choice == "3":
            models = cfg.data["models"]
            if not models:
                print(col("(暂无模型)", C.DIM))
                continue
            for i, m in enumerate(models, 1):
                print(col(f" {i}. {m['display']}", C.CYAN))
        elif choice == "4":
            models = cfg.data["models"]
            if not models:
                print(col("(暂无模型)", C.DIM))
                continue
            for i, m in enumerate(models, 1):
                print(col(f" {i}. {m['display']}", C.CYAN))
            sel = ask(col("输入要删除的模型序号（多个用逗号分隔）: ", C.YELLOW)).strip()
            idxs = []
            for part in sel.replace("，", ",").split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= len(models):
                    idxs.append(int(part) - 1)
            if not idxs:
                print(col("无效选择。", C.YELLOW))
                continue
            for i in sorted(idxs, reverse=True):
                m = models[i]
                cfg.data["models"].remove(m)
                cfg.data["histories"].pop(m["id"], None)
                print(col(f"  ✗ 已删除模型: {m['display']}", C.GREEN))
            cfg.save()


def clear_history_menu(cfg):
    models = cfg.data["models"]
    if not models:
        print(col("(暂无模型)", C.DIM))
        return
    print(col("选择要清除历史的模型（输入 0 清除全部）:", C.CYAN))
    for i, m in enumerate(models, 1):
        n = len(cfg.history(m["id"]))
        print(col(f" {i}. {m['display']}  (历史 {n} 条)", C.CYAN))
    sel = ask(col("请选择: ", C.YELLOW)).strip()
    if sel == "0":
        cfg.data["histories"] = {}
        cfg.save()
        print(col("已清除全部对话历史。", C.GREEN))
    elif sel.isdigit() and 1 <= int(sel) <= len(models):
        m = models[int(sel) - 1]
        cfg.data["histories"][m["id"]] = []
        cfg.save()
        print(col(f"已清除“{m['display']}”的对话历史。", C.GREEN))
    else:
        print(col("无效选择。", C.YELLOW))


# ==================== 插件管理 ====================
def plugin_menu(cfg):
    """主菜单 4：管理插件（查看/导入/启停/删除）。"""
    while True:
        clear_screen()
        print(col("========== 管理插件 ==========", C.BLUE))
        plugins = load_plugins()
        if plugins:
            for i, p in enumerate(plugins, 1):
                tools = p.get("tools", [])
                enabled_n = sum(1 for t in tools if t.get("enabled", True))
                print(col(f" {i}. {p.get('name','?')}  v{p.get('version','?')}", C.CYAN))
                print(col(f"     {p.get('description','')}", C.DIM))
                print(col(f"     工具: {len(tools)} 个 (启用 {enabled_n}) | 文件: {p.get('_file','')}", C.DIM))
        else:
            print(col(" (暂无插件)", C.DIM))
        print()
        print(col(" 1. 导入插件", C.CYAN))
        print(col(" 2. 启用/禁用插件工具", C.CYAN))
        print(col(" 3. 删除插件", C.CYAN))
        print(col(" 0. 返回主菜单", C.CYAN))
        try:
            choice = ask("请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "0":
            return
        if choice == "1":
            import_plugin(cfg)
        elif choice == "2":
            toggle_plugin_tool(cfg)
        elif choice == "3":
            delete_plugin(cfg)
        else:
            print(col("无效选择。", C.YELLOW))


def import_plugin(cfg):
    """导入插件：浏览文件（弹窗）或手动输入路径。"""
    print(col("--- 导入插件 ---", C.MAGENTA))
    print(col(" 1. 浏览文件（弹出文件夹选择）", C.CYAN))
    print(col(" 2. 手动输入文件路径", C.CYAN))
    print(col(" 0. 取消", C.CYAN))
    choice = ask("请选择: ").strip()
    src = None
    if choice == "1":
        src = pick_file(("JSON 插件文件", "*.json"))
    elif choice == "2":
        src = ask(col("插件 JSON 文件路径: ", C.YELLOW)).strip().strip('"')
    if not src:
        print(col("已取消。", C.DIM))
        return
    src = os.path.abspath(os.path.expanduser(src))
    if not os.path.isfile(src):
        print(col(f"文件不存在: {src}", C.RED))
        return
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(col(f"插件文件解析失败: {e}", C.RED))
        return
    ok, err = validate_plugin(data)
    if not ok:
        print(col(f"插件格式无效: {err}", C.RED))
        return
    fn = data["id"] + ".json"
    dst = os.path.join(plugins_dir(), fn)
    if os.path.exists(dst):
        ans = ask(col(f"插件“{data['name']}”已存在，覆盖？[y/n]: ", C.YELLOW)).strip().lower()
        if ans not in ("y", "yes"):
            print(col("已取消。", C.DIM))
            return
    try:
        import shutil
        shutil.copyfile(src, dst)
    except Exception as e:
        print(col(f"导入失败: {e}", C.RED))
        return
    print(col(f"插件“{data['name']}”已导入并生效。", C.GREEN))


def pick_file(filetypes):
    """弹出文件选择窗口，返回所选文件路径或 None。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askopenfilename(title="选择插件文件", filetypes=[filetypes])
        root.destroy()
        return folder if folder else None
    except Exception:
        return None


def toggle_plugin_tool(cfg):
    """启用/禁用插件中的工具。"""
    plugins = load_plugins()
    if not plugins:
        print(col("(暂无插件)", C.DIM))
        return
    print(col("选择插件:", C.CYAN))
    for i, p in enumerate(plugins, 1):
        print(col(f" {i}. {p.get('name','?')}", C.CYAN))
    sel = ask(col("请选择: ", C.YELLOW)).strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(plugins)):
        print(col("无效选择。", C.YELLOW))
        return
    p = plugins[int(sel) - 1]
    path = os.path.join(plugins_dir(), p["_file"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(col(f"读取失败: {e}", C.RED))
        return
    tools = data.get("tools", [])
    print(col(f"插件“{data.get('name','')}”的工具:", C.CYAN))
    for i, t in enumerate(tools, 1):
        state = "启用" if t.get("enabled", True) else "禁用"
        print(col(f" {i}. {t.get('name')}  [{state}]  {t.get('description','')}", C.CYAN))
    sel2 = ask(col("输入要切换的工具序号: ", C.YELLOW)).strip()
    if not sel2.isdigit() or not (1 <= int(sel2) <= len(tools)):
        print(col("无效选择。", C.YELLOW))
        return
    t = tools[int(sel2) - 1]
    t["enabled"] = not t.get("enabled", True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(col(f"已{'启用' if t['enabled'] else '禁用'}工具“{t.get('name')}”。", C.GREEN))
    except Exception as e:
        print(col(f"保存失败: {e}", C.RED))


def delete_plugin(cfg):
    """删除插件文件。"""
    plugins = load_plugins()
    if not plugins:
        print(col("(暂无插件)", C.DIM))
        return
    print(col("选择要删除的插件:", C.CYAN))
    for i, p in enumerate(plugins, 1):
        print(col(f" {i}. {p.get('name','?')}", C.CYAN))
    sel = ask(col("请选择: ", C.YELLOW)).strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(plugins)):
        print(col("无效选择。", C.YELLOW))
        return
    p = plugins[int(sel) - 1]
    ans = ask(col(f"确认删除插件“{p.get('name','')}”？[y/n]: ", C.RED)).strip().lower()
    if ans not in ("y", "yes"):
        print(col("已取消。", C.DIM))
        return
    try:
        os.remove(os.path.join(plugins_dir(), p["_file"]))
        print(col(f"插件“{p.get('name','')}”已删除。", C.GREEN))
    except Exception as e:
        print(col(f"删除失败: {e}", C.RED))


# ==================== 入口 ====================
def _main_loop():
    cfg = Config()
    ensure_default_plugins()
    clear_screen()
    print(col(f"===== {APP_NAME} v{APP_VERSION} =====", C.BOLD))
    print(col(f"配置: {config_path()}", C.DIM))
    while True:
        main_menu(cfg)
        try:
            choice = ask(col("请选择: ", C.GREEN)).strip()
        except (EOFError, KeyboardInterrupt):
            print(col("\n再见！", C.BLUE))
            break
        try:
            if choice == "1":
                connect_menu(cfg)
            elif choice == "2":
                add_provider_menu(cfg)
            elif choice == "3":
                settings_menu(cfg)
            elif choice == "4":
                plugin_menu(cfg)
            elif choice in ("0", "q", "exit"):
                print(col("再见！", C.BLUE))
                break
            else:
                print(col("无效选择。", C.YELLOW))
        except (EOFError, KeyboardInterrupt):
            print(col("\n返回主菜单。", C.BLUE))
            break
        except Exception as e:
            # 任何菜单操作出错都回到主菜单，不让程序崩溃
            print(col(f"\n[操作出错] {e}（已返回主菜单）", C.RED))


def main():
    """程序入口：最外层保护，任何未预期异常都不会让程序直接闪退。"""
    try:
        _main_loop()
    except KeyboardInterrupt:
        print(col("\n再见！", C.BLUE))
    except Exception as e:
        print(col(f"\n[程序异常] {e}", C.RED))
        print(col("（程序已保护性退出，配置已自动保存，可重新启动）", C.DIM))
        try:
            input(col("按回车退出...", C.DIM))
        except Exception:
            pass


if __name__ == "__main__":
    main()
