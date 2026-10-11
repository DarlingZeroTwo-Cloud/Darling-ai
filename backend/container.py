"""darling-ai 3.0 容器工作区（Windows=WSL / Linux=Docker）。

- Windows：检测已安装的 WSL 发行版，支持在其中执行命令，提供微软商店安装链接。
- Linux：检测 docker，未安装时自动尝试安装（apt/dnf/pacman/apk + 国内镜像加速），
  然后按发行版镜像在容器内执行命令。
零第三方依赖，全部走标准库 + 平台命令。
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys

DOCKER_MIRRORS = [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.m.daocloud.io",
    "https://dockerproxy.net",
]


def is_windows():
    return platform.system() == "Windows" or os.name == "nt"


def _decode(data):
    """智能解码：UTF-16LE（wsl 输出）→ UTF-8 → GBK 兜底。"""
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16-le" if data[:2] == b"\xff\xfe" else "utf-16-be",
                               errors="replace").lstrip("\ufeff")
        except Exception:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk", errors="replace")
        except Exception:
            return data.decode("utf-8", errors="replace")


def _run(cmd, timeout=60, cwd=None):
    """执行命令，返回 (ok, stdout+stderr)。不抛异常。"""
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str),
                           capture_output=True, timeout=timeout, cwd=cwd,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = _decode(p.stdout) + _decode(p.stderr)
        return p.returncode == 0, out.strip()
    except FileNotFoundError:
        return False, "命令不存在"
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as e:
        return False, f"执行异常: {e}"


# ==================== WSL（Windows） ====================

def wsl_installed():
    ok, _ = _run("wsl --version", timeout=15)
    if not ok:
        ok, _ = _run("wsl -l -v", timeout=15)
    return ok


def wsl_distros():
    """返回 [{"name": str, "state": str, "default": bool}, ...]"""
    ok, out = _run("wsl -l -v", timeout=30)
    if not ok:
        return []
    distros = []
    for line in out.splitlines():
        line = line.replace("\x00", "").strip()
        if not line or "Windows" in line or "WSL" in line:
            continue
        if line.upper().startswith(("NAME", "VERSION", "默认", "名称")):
            continue
        m = re.match(r"^(\*?)\s*([A-Za-z0-9_\-\. ]+?)\s+(Running|Stopped|Installing|Uninstalling|Unknown|Default|已停止|正在运行|已安装)\s*", line)
        if not m:
            # 兜底：跳过星号取第一个 token 作为名字
            parts = [p for p in line.split() if p and p != "*"]
            if not parts:
                continue
            distros.append({"name": parts[0], "state": "", "default": False})
            continue
        distros.append({"name": m.group(2).strip(), "state": m.group(3),
                        "default": m.group(1) == "*"})
    return distros


def wsl_exec(distro, command, timeout=300, workspace=None):
    """在指定 WSL 发行版中执行命令。workspace 映射为 /mnt/c/... 或 /work。"""
    if not command or not command.strip():
        return False, "命令为空"
    safe = command.replace('"', '\\"')
    if workspace and os.path.isabs(workspace):
        wsl_path = workspace.replace("\\", "/")
        if wsl_path[1:2] == ":":
            drive = wsl_path[0].lower()
            wsl_path = "/mnt/" + drive + wsl_path[2:]
        full = f'cd "{wsl_path}" 2>/dev/null; {command}'
    else:
        full = command
    ok, out = _run(f'wsl -d {distro} -- sh -lc "{safe2(full)}"', timeout=timeout)
    return ok, out


def safe2(s):
    return s.replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def wsl_store_link():
    """微软商店 WSL 发行版页。"""
    return "ms-windows-store://pdp/?ProductId=9P9TQF7MRM4R"


def wsl_install_hint():
    return ("未检测到可用的 WSL 发行版。请先在微软商店安装 WSL（见下方链接），"
            "或运行 wsl --install 安装默认 Ubuntu。")


# ==================== Docker（Linux / 通用） ====================

def docker_installed():
    ok, _ = _run("docker --version", timeout=15)
    if not ok:
        ok, _ = _run("docker version --format '{{.Server.Version}}'", timeout=20)
    return ok


def docker_images():
    ok, out = _run("docker images --format '{{.Repository}}:{{.Tag}}'", timeout=30)
    if not ok:
        return []
    return [i.strip() for i in out.splitlines() if i.strip()]


def install_docker(ask_sudo=True):
    """自动安装 Docker 并配置国内镜像加速。返回 (ok, 输出说明)。"""
    lines = []
    # 尝试免密 sudo / root
    def run_as_root(cmd, timeout=300):
        if os.name == "nt":
            return _run(cmd, timeout=timeout)
        if os.geteuid() == 0:
            return _run(cmd, timeout=timeout)
        if ask_sudo:
            return _run(f"sudo {cmd}", timeout=timeout)
        return False, "需要 root 权限"
    distro = ""
    for f in ("/etc/os-release", "/etc/alpine-release"):
        if os.path.exists(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    content = fh.read()
                for k, v in (("ID=", "id"), ("ID_LIKE=", "id_like")):
                    for line in content.splitlines():
                        if line.startswith(k):
                            distro += " " + line[len(k):].strip().strip('"')
            except Exception:
                pass
            break
    d = distro.lower()
    if "debian" in d or "ubuntu" in d or not d:
        ok, out = run_as_root(
            "apt-get update -y && apt-get install -y docker.io && "
            "systemctl enable docker 2>/dev/null; systemctl start docker 2>/dev/null; "
            "docker --version")
        lines.append(("apt", ok, out))
    elif "arch" in d or "manjaro" in d:
        ok, out = run_as_root(
            "pacman -Sy --noconfirm docker && systemctl enable docker 2>/dev/null; "
            "systemctl start docker 2>/dev/null; docker --version")
        lines.append(("pacman", ok, out))
    elif "fedora" in d or "rhel" in d or "centos" in d:
        ok, out = run_as_root(
            "dnf install -y docker-ce docker-ce-cli containerd.io 2>/dev/null || "
            "dnf install -y docker; systemctl enable docker 2>/dev/null; "
            "systemctl start docker 2>/dev/null; docker --version")
        lines.append(("dnf", ok, out))
    elif "alpine" in d:
        ok, out = run_as_root(
            "apk add --no-cache docker && rc-update add dockerd 2>/dev/null; "
            "dockerd >/dev/null 2>&1 & sleep 3; docker --version")
        lines.append(("apk", ok, out))
    else:
        return False, f"未识别发行版({distro})，请手动安装 Docker。"
    # 配置国内镜像加速
    cfg = {"registry-mirrors": DOCKER_MIRRORS}
    ok_cfg, out_cfg = run_as_root(
        "mkdir -p /etc/docker && "
        f"echo '{json.dumps(cfg)}' > /etc/docker/daemon.json && "
        "systemctl restart docker 2>/dev/null; pkill dockerd 2>/dev/null; "
        "dockerd >/dev/null 2>&1 & sleep 2; docker info 2>/dev/null | grep -i mirror || true",
        timeout=120)
    lines.append(("mirror", ok_cfg, out_cfg))
    return docker_installed(), "\n".join(f"[{k}] {o}" for k, ok, o in lines)


def docker_exec(image, command, timeout=600, workspace=None):
    """在 docker 容器中执行命令，挂载工作区到 /work。"""
    if not image:
        return False, "未选择容器镜像"
    if not command or not command.strip():
        return False, "命令为空"
    vol = ""
    if workspace and os.path.isabs(workspace):
        vol = f' -v "{workspace}:/work"'
    cmd = (f'docker run --rm --network host -i {vol} '
           f'-w /work {image} sh -lc {json.dumps(command)}')
    return _run(cmd, timeout=timeout)


def detect():
    """返回平台容器能力概览。"""
    info = {"platform": platform.system(), "wsl": False, "wsl_distros": [],
            "docker": False, "docker_images": []}
    if is_windows():
        info["wsl"] = wsl_installed()
        info["wsl_distros"] = wsl_distros()
        info["docker"] = docker_installed()
        info["docker_images"] = docker_images()
    else:
        info["docker"] = docker_installed()
        info["docker_images"] = docker_images()
    return info
