# -*- coding: utf-8 -*-
"""1688 工厂/商家店铺搜索。

按关键词分页抓取 1688 工厂搜索结果页
（https://s.1688.com/company/pc/factory_search.htm），解析店铺名称、所在地、
经营年限、简介、会员 ID 以及响应率/履约率/回头率等指标，结果导出为 CSV，
供人工筛选优质店铺。

前置条件：
    1. Chrome 已以远程调试模式启动（见 browser.py 模块说明）并登录 1688；
    2. 可选：项目根目录放置 stealth.min.js 以降低自动化特征被识别的概率。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import random
import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from browser import connect_chrome
from config import FACTORY_CSV_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

FACTORY_SEARCH_URL = "https://s.1688.com/company/pc/factory_search.htm"
DEFAULT_KEYWORD = "饰品"
DEFAULT_MAX_PAGES = 20
# 页面加载失败时的重试次数
MAX_RETRIES = 3
# 卡片中 memberId 的埋点字段格式：^item_id@<会员ID>^object_type@...
MEMBER_ID_PATTERN = re.compile(r"\^item_id@(.*?)\^object_type@")


def build_search_url(keyword: str, page: int) -> str:
    """构造工厂搜索结果页 URL（关键词按 GBK 编码，与站点一致）。"""
    encoded_keyword = quote(keyword, encoding="gbk")
    return f"{FACTORY_SEARCH_URL}?keywords={encoded_keyword}&beginPage={page}"


def _text(card, selector: str) -> str:
    """读取卡片内某个选择器的文本，节点缺失时返回空字符串。"""
    node = card.select_one(selector)
    return node.text.strip() if node and node.text else ""


def parse_html(soup: BeautifulSoup) -> list:
    """解析一页工厂搜索结果 HTML，返回店铺信息列表。"""
    factory_list = []
    container = soup.select_one("div.company-original-offer-list")
    if not container:
        logger.warning("未找到工厂搜索结果列表节点")
        return factory_list

    for card in container.find_all("div", recursive=False):
        try:
            report_node = card.select_one("div.space-factory-card")
            report = report_node.get("data-aplus-report", "") if report_node else ""
            match = MEMBER_ID_PATTERN.search(report)
            member_id = match.group(1) if match else ""

            # 多个 div.rate 按文字内容区分具体指标
            response_rate = fulfillment_rate = repurchase_rate = ""
            for rate in card.select("div.rate"):
                rate_text = rate.text.strip() if rate.text else ""
                if "响应率" in rate_text:
                    response_rate = rate_text
                elif "履约率" in rate_text:
                    fulfillment_rate = rate_text
                elif "回头率" in rate_text:
                    repurchase_rate = rate_text

            factory_list.append({
                "title": _text(card, "div.title"),
                "location": _text(card, "div.location"),
                "year_text": _text(card, "span.year-text"),
                "desc": _text(card, "div.desc"),
                "member_id": member_id,
                "response_rate": response_rate,
                "fulfillment_rate": fulfillment_rate,
                "repurchase_rate": repurchase_rate,
            })
        except Exception as exc:
            logger.warning("解析工厂卡片失败，已跳过：%s", exc)

    return factory_list


def fetch_factory_page(driver, url: str) -> list:
    """打开一个搜索结果页并解析，失败自动重试，返回店铺信息列表。"""
    factories = []
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(url)
            # 第一层：等待文档加载完成
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            # 第二层：等待结果列表渲染
            WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located(
                    (By.CLASS_NAME, "company-original-offer-list")
                )
            )
        except Exception as exc:
            logger.warning("第 %d 次打开页面超时：%s", attempt, exc)

        # 随机等待，降低请求频率
        time.sleep(random.uniform(3.0, 6.0))
        factories = parse_html(BeautifulSoup(driver.page_source, "html.parser"))
        if factories:
            break
        logger.info("第 %d 次解析结果为空，准备重试", attempt)

    return factories


def save_to_csv(data: list, filepath) -> None:
    """将店铺列表写入 UTF-8-BOM 编码的 CSV（Excel 直接打开不乱码）。"""
    if not data:
        logger.warning("没有数据可存储")
        return
    with open(filepath, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    logger.info("已写入 %s，共 %d 条记录", filepath, len(data))


def run(driver, keyword: str, max_pages: int, output_file: str) -> list:
    """按关键词抓取多页工厂信息并导出 CSV。"""
    all_factories = []
    for page in range(1, max_pages + 1):
        url = build_search_url(keyword, page)
        factories = fetch_factory_page(driver, url)
        all_factories.extend(factories)
        logger.info(
            "关键词「%s」第 %d/%d 页完成，本页 %d 条，累计 %d 条",
            keyword, page, max_pages, len(factories), len(all_factories),
        )
        time.sleep(1)

    save_to_csv(all_factories, output_file)
    return all_factories


def main() -> None:
    parser = argparse.ArgumentParser(description="按关键词搜索 1688 工厂/商家店铺")
    parser.add_argument(
        "-k", "--keyword", default=DEFAULT_KEYWORD,
        help=f"搜索关键词，默认：{DEFAULT_KEYWORD}",
    )
    parser.add_argument(
        "-p", "--pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"抓取页数，默认：{DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="CSV 输出路径，默认输出到 data/factory 目录",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ensure_data_dirs()

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = args.output or str(
        FACTORY_CSV_DIR / f"1688_factory_{args.keyword}_{timestamp}.csv"
    )

    driver = connect_chrome()
    try:
        run(driver, args.keyword, args.pages, output_file)
    except Exception as exc:
        logger.exception("执行过程中出错：%s", exc)
    finally:
        # 仅断开调试连接，不关闭用户自己启动的 Chrome
        try:
            driver.quit()
        except Exception:
            pass
    logger.info("全部完成")


if __name__ == "__main__":
    main()
