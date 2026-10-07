# -*- coding: utf-8 -*-
"""1688 商品讲解视频批量下载。

在 1688 官方采购助手的「优选工作台 - 商品管理」页面中，逐个对商品执行
「更多操作 → 查看货源」，在打开的 1688 商品详情页中提取 <video> 地址，
复用浏览器 Cookie 通过 requests 流式下载 MP4（直接裸请求会返回 403）。

支持两种模式：
    1. 全量模式（默认）：从商品管理列表第一页起逐页下载（--max-pages 控制页数）；
    2. 指定 ID 模式（--ids product_ids.txt）：先按商品 ID 搜索，再逐个下载。

前置条件：
    Chrome 已以远程调试模式启动，登录采购助手并手动打开「优选工作台」的
    商品管理列表页（页面保持在表格视图即可，脚本会自动切入 iframe）。
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import requests
from selenium.common.exceptions import NoSuchElementException, NoSuchFrameException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from browser import connect_chrome
from config import VIDEO_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

# 工作台窗口标题与 iframe 名称（1688 页面升级后可能变化）
WORKBENCH_WINDOW_TITLE = "优选工作台 - 应用容器"
WORKBENCH_IFRAME_NAME = "fuwu_work_isv_container_frame"

DEFAULT_MAX_PAGES = 6
PAGE_WAIT_SECONDS = 3
DETAIL_OPEN_WAIT_SECONDS = 10

# 商品行 / 操作菜单 / 分页按钮定位器
ROW_SELECTOR = "tr.next-table-row"
PRODUCT_ID_XPATH = "(.//div[contains(., '商品ID') and not(descendant::div)])[last()]"
MORE_ACTION_XPATH = "(.//div[contains(., '更多操作') and not(descendant::div)])[last()]"
VIEW_SOURCE_XPATH = "(.//div[contains(., '查看货源') and not(descendant::div)])[last()]"
NEXT_PAGE_XPATH = (
    "//*[@id='goodsManageContentBox']/div/div/div[2]/ul/"
    "li[@title='下一页']/button"
)
SEARCH_INPUT_SELECTOR = "input[placeholder*='商品ID']"


def switch_to_window_by_title(driver, target_title: str) -> bool:
    """按标题切换浏览器窗口，未找到时切回原窗口。"""
    original = driver.current_window_handle
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if target_title.lower() in driver.title.lower():
            return True
    driver.switch_to.window(original)
    return False


def get_direct_child_iframes(driver, parent_iframe_name: str) -> list:
    """切入指定 name 的父 iframe，并返回其直接子 iframe 元素列表。"""
    try:
        driver.switch_to.frame(parent_iframe_name)
        return driver.find_elements(By.TAG_NAME, "iframe")
    except NoSuchFrameException:
        logger.error("找不到 name 为 %r 的 iframe", parent_iframe_name)
        return []


def enter_workbench_iframe(driver) -> bool:
    """切换到工作台窗口并进入商品管理表格所在的子 iframe。"""
    if not switch_to_window_by_title(driver, WORKBENCH_WINDOW_TITLE):
        logger.error("未找到「%s」窗口，请先手动打开商品管理页",
                     WORKBENCH_WINDOW_TITLE)
        return False
    child_iframes = get_direct_child_iframes(driver, WORKBENCH_IFRAME_NAME)
    if not child_iframes:
        logger.error("工作台 iframe 未加载")
        return False
    driver.switch_to.frame(child_iframes[0])
    return True


def _back_to_workbench_iframe(driver) -> None:
    """关闭详情标签页后，切回工作台窗口并重新进入 iframe。"""
    if len(driver.window_handles) > 1:
        driver.close()
    driver.switch_to.window(driver.window_handles[0])
    child_iframes = get_direct_child_iframes(driver, WORKBENCH_IFRAME_NAME)
    if child_iframes:
        driver.switch_to.frame(child_iframes[0])
    time.sleep(PAGE_WAIT_SECONDS)


def download_video(driver, video_url: str, page_url: str,
                   product_id: str, save_dir: Path) -> bool:
    """携带浏览器 Cookie 与 Referer 流式下载视频，返回是否成功。"""
    save_path = save_dir / f"{product_id}.mp4"
    if save_path.exists():
        logger.info("视频已存在，跳过：%s", save_path.name)
        return True

    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    headers = {
        "User-Agent": driver.execute_script("return navigator.userAgent;"),
        "Referer": page_url,
    }
    try:
        with session.get(video_url, headers=headers, stream=True, timeout=60) as resp:
            resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("视频下载成功：%s", save_path.name)
        return True
    except Exception as exc:
        logger.warning("视频 %s 下载失败：%s", product_id, exc)
        return False
    finally:
        session.close()


def _open_detail_and_download(driver, row, save_dir: Path,
                              expected_id: str | None = None) -> None:
    """对表格中的一行商品：悬停「更多操作」→「查看货源」，在新标签页下载视频。"""
    # 读取商品 ID
    id_node = row.find_element(By.XPATH, PRODUCT_ID_XPATH)
    product_id = id_node.text.strip().replace("商品ID：", "")
    if expected_id and product_id != expected_id:
        logger.warning("页面商品ID %s 与搜索ID %s 不匹配", product_id, expected_id)
    logger.info("处理商品ID：%s", product_id)

    # 悬停「更多操作」唤出浮层
    more_node = row.find_element(By.XPATH, MORE_ACTION_XPATH)
    driver.execute_script(
        "arguments[0].dispatchEvent(new MouseEvent('mouseover',"
        " {'view': window, 'bubbles': true, 'cancelable': true}));",
        more_node,
    )
    view_source = WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.XPATH, VIEW_SOURCE_XPATH))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", view_source)
    time.sleep(0.5)
    ActionChains(driver).move_to_element(view_source).click().perform()
    time.sleep(DETAIL_OPEN_WAIT_SECONDS)

    # 新标签页为 1688 商品详情页，提取 video 地址
    driver.switch_to.window(driver.window_handles[-1])
    page_url = driver.current_url
    logger.info("已打开货源页：%s", page_url)
    try:
        video_tag = driver.find_element(By.TAG_NAME, "video")
        video_url = video_tag.get_attribute("src")
        if video_url:
            download_video(driver, video_url, page_url, product_id, save_dir)
        else:
            logger.info("商品 %s 没有视频（video src 为空）", product_id)
    except NoSuchElementException:
        logger.info("商品 %s 没有视频", product_id)
    except Exception as exc:
        logger.warning("商品 %s 处理异常：%s", product_id, exc)
    finally:
        _back_to_workbench_iframe(driver)


def _iter_current_page_rows(driver):
    return driver.find_elements(By.CSS_SELECTOR, ROW_SELECTOR)


def _goto_next_page(driver) -> bool:
    """点击分页器的「下一页」，没有按钮时返回 False。"""
    try:
        next_button = driver.find_element(By.XPATH, NEXT_PAGE_XPATH)
    except NoSuchElementException:
        logger.info("已到最后一页")
        return False
    next_button.click()
    time.sleep(PAGE_WAIT_SECONDS)
    return True


def _search_product(driver, product_id: str) -> None:
    """在商品管理页的搜索框中按商品 ID 检索并回车。"""
    search_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, SEARCH_INPUT_SELECTOR))
    )
    search_input.click()
    search_input.send_keys(Keys.CONTROL, "a")
    search_input.send_keys(Keys.DELETE)
    search_input.send_keys(product_id)
    time.sleep(0.5)
    search_input.send_keys(Keys.ENTER)
    time.sleep(PAGE_WAIT_SECONDS)


def download_all(driver, save_dir, max_pages: int = DEFAULT_MAX_PAGES) -> None:
    """从第一页起逐页下载商品管理列表中的全部商品视频。"""
    if not enter_workbench_iframe(driver):
        return
    for page in range(1, max_pages + 1):
        rows = _iter_current_page_rows(driver)
        logger.info("第 %d/%d 页，共 %d 个商品", page, max_pages, len(rows))
        for row in rows:
            try:
                _open_detail_and_download(driver, row, save_dir)
            except Exception as exc:
                logger.warning("商品行处理失败，跳过：%s", exc)
                # 异常后标签页/iframe 上下文可能错乱，重新进入
                enter_workbench_iframe(driver)
        if page < max_pages and not _goto_next_page(driver):
            break


def download_by_ids(driver, product_ids: list, save_dir) -> None:
    """按给定商品 ID 列表逐个搜索并下载视频。"""
    if not enter_workbench_iframe(driver):
        return
    for index, product_id in enumerate(product_ids, start=1):
        logger.info("===== %d/%d 搜索商品ID：%s =====",
                    index, len(product_ids), product_id)
        try:
            _search_product(driver, product_id)
            rows = _iter_current_page_rows(driver)
            if not rows:
                logger.warning("未搜索到商品 %s", product_id)
                continue
            _open_detail_and_download(driver, rows[0], save_dir, expected_id=product_id)
        except Exception as exc:
            logger.warning("商品 %s 处理失败：%s", product_id, exc)
            enter_workbench_iframe(driver)


def read_product_ids(filepath: str) -> list:
    """读取商品 ID 清单（每行一个，空行与 # 开头的注释忽略）。"""
    ids = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    logger.info("从 %s 读取到 %d 个商品ID", filepath, len(ids))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="1688 商品讲解视频批量下载")
    parser.add_argument(
        "--ids", default=None,
        help="商品 ID 清单文件（每行一个）；不指定时走全量分页模式",
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"全量模式最多抓取页数，默认：{DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "-o", "--out-dir", default=None,
        help="视频保存目录，默认 data/videos",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ensure_data_dirs()
    save_dir = Path(args.out_dir) if args.out_dir else VIDEO_DIR
    save_dir.mkdir(parents=True, exist_ok=True)

    driver = connect_chrome()
    try:
        if args.ids:
            download_by_ids(driver, read_product_ids(args.ids), save_dir)
        else:
            download_all(driver, save_dir, args.max_pages)
    finally:
        # 仅断开调试连接，不关闭用户自己启动的 Chrome
        try:
            driver.quit()
        except Exception:
            pass
    logger.info("全部完成")


if __name__ == "__main__":
    main()
