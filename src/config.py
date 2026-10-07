# -*- coding: utf-8 -*-
"""全局配置。

所有敏感信息（数据库账号密码、本地目录等）统一通过环境变量或项目根目录下的
``.env`` 文件读取，代码仓库中不包含任何真实凭据。使用方式：

    1. 复制项目根目录下的 ``.env.example`` 为 ``.env``；
    2. 按实际环境修改其中的配置项。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# 项目根目录（src 的上一级），所有相对路径均基于它解析，与运行时工作目录无关
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """加载项目根目录下的 .env 文件（未安装 python-dotenv 时静默跳过）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_ROOT / ".env")


_load_dotenv()


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# MySQL 数据库配置（对应通过 1688 采购助手导出 Excel 后导入的商品库）
# ---------------------------------------------------------------------------
MYSQL_CONFIG = {
    "host": _env("MYSQL_HOST", "127.0.0.1"),
    "port": int(_env("MYSQL_PORT", "3306")),
    "user": _env("MYSQL_USER", "root"),
    "password": _env("MYSQL_PASSWORD", ""),
    "database": _env("MYSQL_DATABASE", "ebusiness"),
    "charset": _env("MYSQL_CHARSET", "utf8mb4"),
}

# ---------------------------------------------------------------------------
# Chrome 远程调试配置（脚本附着到已登录的 Chrome，不另起浏览器）
# ---------------------------------------------------------------------------
CHROME_DEBUG_ADDRESS = _env("CHROME_DEBUG_ADDRESS", "127.0.0.1:9222")

# stealth.min.js 用于隐藏 Selenium 自动化特征，需自行下载后放到项目根目录
STEALTH_JS_PATH = Path(
    _env("STEALTH_JS_PATH", str(PROJECT_ROOT / "stealth.min.js"))
)

# ---------------------------------------------------------------------------
# 运行期数据目录（CSV、图片、视频、日志等），默认全部放在项目内的 data 目录
# ---------------------------------------------------------------------------
DATA_DIR = Path(_env("DATA_DIR", str(PROJECT_ROOT / "data")))
FACTORY_CSV_DIR = DATA_DIR / "factory"       # 工厂搜索结果 CSV
SEARCH_PIC_DIR = DATA_DIR / "search_pic"     # 用于拼多多以图搜货的 1688 图片
PDD_RESULT_DIR = DATA_DIR / "pdd_result"     # 拼多多搜索结果截图
VIDEO_DIR = DATA_DIR / "videos"              # 下载的 1688 商品视频
LOG_DIR = DATA_DIR / "logs"                  # 运行日志


def ensure_data_dirs() -> None:
    """创建所有运行期数据目录（已存在则忽略）。"""
    for directory in (
        DATA_DIR,
        FACTORY_CSV_DIR,
        SEARCH_PIC_DIR,
        PDD_RESULT_DIR,
        VIDEO_DIR,
        LOG_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
