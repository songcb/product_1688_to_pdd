# -*- coding: utf-8 -*-
"""Chrome 浏览器连接公共模块。

所有采集脚本均通过 Chrome 的远程调试端口（默认 127.0.0.1:9222）附着到一个
已经手动启动并完成登录的 Chrome 实例，复用其登录态，避免反复登录与验证码。
启动方式（Windows）::

    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
        --remote-debugging-port=9222 ^
        --user-data-dir="C:\\chrome-debug-profile"
"""
from __future__ import annotations

import logging
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config import CHROME_DEBUG_ADDRESS, STEALTH_JS_PATH

logger = logging.getLogger(__name__)


def connect_chrome(
    debugger_address: Optional[str] = None,
    stealth_js_path: Optional[str] = None,
):
    """连接到远程调试模式的 Chrome。

    Args:
        debugger_address: Chrome 远程调试地址，默认取配置中的 CHROME_DEBUG_ADDRESS。
        stealth_js_path: stealth.min.js 路径；文件不存在时仅告警，不阻断运行。

    Returns:
        selenium.webdriver.Chrome 实例。
    """
    address = debugger_address or CHROME_DEBUG_ADDRESS
    options = Options()
    options.add_experimental_option("debuggerAddress", address)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)

    stealth_path = stealth_js_path or STEALTH_JS_PATH
    if stealth_path.exists():
        with open(stealth_path, "r", encoding="utf-8") as f:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": f.read()}
            )
        logger.info("已注入 stealth.min.js")
    else:
        logger.warning(
            "未找到 stealth.min.js（%s），已跳过反检测脚本注入；"
            "如遇风控拦截请自行下载该文件。",
            stealth_path,
        )

    logger.info("已连接 Chrome 远程调试端口 %s", address)
    return driver
