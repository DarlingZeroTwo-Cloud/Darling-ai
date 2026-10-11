"""darling-ai 3.0 工作区工具集 + 内置浏览器（零新增重依赖）。

- 文件工具：file_read / file_write / dir_list（严格限定在工作区内，防路径穿越）
- git 工具：git_status / git_diff / git_commit
- 浏览器：browser_open / browser_read（requests 抓正文；若环境装了 playwright 则可用截图）
"""
import base64
import html.parser
import json
import os
import re
import shutil
import subprocess

# ==================== 路径安全 ====================

def _safe_resolve(workspace, rel):
    """把 rel（相对或绝对路径）解析到 workspace 内。越界返回 None。"""
    if not workspace:
        workspace = "."
    base = os.path.abspath(workspace)
    if not os.path.isdir(base):
        base = os.path.dirname(base) or "."
    p = os.path.abspath(os.path.join(base, rel)) if not os.path.isabs(rel) \
        else os.path.abspath(rel)
    if p == base or p.startswith(base + os.sep):
        return p
    return None


# ==================== 文件工具 ====================

def handler_file_read(content, workspace=None):
    """读文件，content=文件路径（相对工作区或绝对）。"""
    path = _safe_resolve(workspace, content.strip())
    if not path:
        return False, "路径超出工作区范围，已拒绝"
    if not os.path.isfile(path):
        return False, f"文件不存在: {content}"
    size = os.path.getsize(path)
    if size > 512 * 1024:
        return False, f"文件过大({size // 1024}KB)，超过 512KB 限制"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return True, f.read(30000)
    except Exception as e:
        return False, f"读取失败: {e}"


def handler_file_write(content, workspace=None):
    """写文件，content 形如：路径\n\n内容（首行为文件路径）。"""
    lines = content.split("\n", 1)
    rel = lines[0].strip()
    body = lines[1] if len(lines) > 1 else ""
    path = _safe_resolve(workspace, rel)
    if not path:
        return False, "路径超出工作区范围，已拒绝"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return True, f"已写入 {len(body)} 字符 -> {rel}"
    except Exception as e:
        return False, f"写入失败: {e}"


def handler_dir_list(content, workspace=None):
    """列出目录，content=目录路径（默认当前工作区）。"""
    rel = content.strip() or "."
    path = _safe_resolve(workspace, rel)
    if not path:
        return False, "路径超出工作区范围，已拒绝"
    if not os.path.isdir(path):
        return False, f"目录不存在: {content or workspace}"
    try:
        items = sorted(os.listdir(path))
        rows = []
        for it in items[:300]:
            full = os.path.join(path, it)
            if os.path.isdir(full):
                rows.append(f"[目录] {it}")
            else:
                sz = os.path.getsize(full)
                rows.append(f"[文件] {it} ({sz} B)")
        return True, "\n".join(rows)
    except Exception as e:
        return False, f"列目录失败: {e}"


# ==================== git 工具 ====================

def _git(workspace, args, timeout=60):
    if not workspace:
        return False, "未设置工作区"
    if not os.path.isdir(os.path.join(workspace, ".git")):
        return False, "当前工作区不是 git 仓库（缺少 .git）"
    try:
        p = subprocess.run(["git"] + args, cwd=workspace, capture_output=True,
                           timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode == 0, (out + err).strip()
    except FileNotFoundError:
        return False, "未安装 git"
    except subprocess.TimeoutExpired:
        return False, "git 执行超时"
    except Exception as e:
        return False, f"git 异常: {e}"


def handler_git_status(content, workspace=None):
    return _git(workspace, ["status", "--short"])


def handler_git_diff(content, workspace=None):
    rel = content.strip() or None
    args = ["diff", "--stat"] if not rel else ["diff", "--", rel]
    return _git(workspace, args)


def handler_git_commit(content, workspace=None):
    msg = content.strip()
    if not msg:
        return False, "提交信息为空"
    ok, out = _git(workspace, ["add", "-A"])
    if not ok:
        return ok, out
    return _git(workspace, ["commit", "-m", msg])


# ==================== 内置浏览器 ====================

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip > 0:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        s = "".join(self.parts)
        s = re.sub(r"[ \t]+", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()


def _fetch_text(url, max_chars=12000):
    import requests
    h = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    r = requests.get(url, headers=h, timeout=(10, 30), verify=False)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "json" in ctype or url.rstrip("/").endswith((".json", ".md", ".txt")):
        return r.text[:max_chars]
    parser = _TextExtractor()
    parser.feed(r.text)
    t = parser.text()
    return t[:max_chars]


def handler_browser_open(content, workspace=None):
    """打开网页并返回正文文本。content=URL。"""
    url = content.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        text = _fetch_text(url)
        return True, f"[已打开] {url}\n\n{text}"
    except Exception as e:
        return False, f"打开网页失败: {e}"


def handler_browser_read(content, workspace=None):
    """读取指定网页正文。content=URL。"""
    return handler_browser_open(content, workspace)


def browser_screenshot(url, out_path):
    """若环境可用 playwright 则截图，否则返回 None。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(url, timeout=30000)
            pg.wait_for_timeout(1200)
            pg.screenshot(path=out_path, full_page=True)
            b.close()
        return out_path
    except Exception:
        return None


def image_b64(path, max_side=900):
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((max_side, max_side))
        import io
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=78)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
