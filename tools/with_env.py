#!/usr/bin/env python3
"""从 .model.env 载入模型凭据并执行命令，不打印任何密钥。

用法:
    .venv/Scripts/python.exe tools/with_env.py -- <命令> [参数...]

规则:
    - 只读取工程根目录下的 .model.env（KEY=VALUE，一行一条，# 开头为注释）
    - 缺任一必需项时直接报错退出，不猜测、不回退到样例模式
    - 输出中只显示已载入的键名与长度，绝不显示值
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".model.env"
REQUIRED = ("FDE_MODEL_URL", "FDE_MODEL_NAME", "FDE_MODEL_KEY")
OPTIONAL = ("FDE_MODEL_PROFILE", "FDE_MODEL_TIMEOUT", "FDE_MODEL_MAX_TOKENS")


def load(path: Path) -> dict:
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    # 密钥可以放在独立文件里，避免与配置混在一起
    if not values.get("FDE_MODEL_KEY") and values.get("FDE_MODEL_KEY_FILE"):
        kp = Path(values["FDE_MODEL_KEY_FILE"]).expanduser()
        if not kp.is_absolute():
            kp = (ROOT / kp).resolve()
        if kp.is_file():
            secret = kp.read_text(encoding="utf-8-sig").strip()
            secret = next((ln.strip() for ln in secret.splitlines() if ln.strip()), "")
            if secret:
                values["FDE_MODEL_KEY"] = secret
                values.setdefault("_KEY_SOURCE", str(kp))
    return values


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("用法: python tools/with_env.py -- <命令> [参数...]", file=sys.stderr)
        return 2
    if not ENV_FILE.is_file():
        print(f"缺少配置文件：{ENV_FILE}", file=sys.stderr)
        return 3

    values = load(ENV_FILE)
    missing = [k for k in REQUIRED if not values.get(k)]
    if missing:
        print("配置文件缺少以下必填项（未填或为空）：" + ", ".join(missing), file=sys.stderr)
        return 4

    env = os.environ.copy()
    env.update({k: v for k, v in values.items() if k.startswith("FDE_") and v})
    shown = []
    for k, v in values.items():
        if not k.startswith("FDE_") or not v:
            continue
        shown.append(f"{k}=已设置({len(v)}字符)" if "KEY" in k else f"{k}={v}")
    print("模型环境：" + " | ".join(shown))
    if values.get("_KEY_SOURCE"):
        print("密钥来源：" + values["_KEY_SOURCE"])
    sys.stdout.flush()

    proc = subprocess.run(args, cwd=str(ROOT), env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
