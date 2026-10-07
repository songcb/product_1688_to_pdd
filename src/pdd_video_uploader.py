# -*- coding: utf-8 -*-
"""拼多多商家后台「商品讲解视频」批量上传。

工作流程：
    1. 附着到远程调试模式的 Chrome（需已登录拼多多商家后台，
       并提前打开「多多视频 - 商品讲解」管理页）；
    2. 读取商品映射表（拼多多商品ID, 商品链接, 视频文件名对应的源商品ID），
       校验本地视频文件是否齐全；
    3. 逐个按拼多多商品 ID 搜索，点击「上传讲解」，
       自动在 Windows 文件对话框中填入视频路径并确认；
    4. 等待「视频上传成功」弹窗并关闭，记录成功/失败结果与日志。

映射表 CSV 格式（首行为表头，逗号或制表符分隔，见 data/product_mapping.example.csv）：

    pdd_product_id,url,video_id
    123456789,https://mobile.yangkeduo.com/goods.html?goods_id=123456789,987654321

其中视频文件名为 ``<video_id>.mp4``（即 get_1688_video.py 下载的文件名，
其值为 1688 商品ID；若经第三方铺货工具映射，填对应来源ID即可）。

注意：仅支持 Windows；Chrome 远程调试启动方式见 browser.py。
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import CHROME_DEBUG_ADDRESS, DATA_DIR, LOG_DIR, VIDEO_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

# 多多视频商家后台窗口标题
TARGET_WINDOW_TITLE = "多多视频｜多多直播"

# ---------------------------------------------------------------------------
# 页面元素定位器（拼多多后台改版后可能需要调整）
# ---------------------------------------------------------------------------
LOCATORS = {
    "product_id_input": (
        By.XPATH,
        "//input[contains(@placeholder, '多个查询以空格或逗号隔开') "
        "or contains(@placeholder, '商品ID')]",
    ),
    "upload_button": (
        By.XPATH,
        "//button[@data-testid='beast-core-button' and contains(text(), '上传讲解')]",
    ),
    "success_modal": (By.XPATH, "//*[contains(text(), '视频上传成功')]"),
    "close_modal_button": (By.CSS_SELECTOR, "div[class*='useUpload_closeBtn']"),
    "loading": (By.XPATH, "//div[contains(@class, 'loading')]"),
}

# 商品之间的上传间隔（秒），避免操作过快
ITEM_INTERVAL_SECONDS = 3
# 上传成功弹窗最长等待时间（秒）
UPLOAD_TIMEOUT_SECONDS = 120


# ---------------------------------------------------------------------------
# Windows 文件对话框处理
# ---------------------------------------------------------------------------
try:
    # 允许使用同目录下更完善的自定义版本
    from file_dialog_handler import FileDialogHandler  # type: ignore
except ImportError:
    class FileDialogHandler:
        """基于 pyautogui 的简易文件对话框处理器。

        在系统「打开」对话框中按 Ctrl+L 定位地址栏，输入完整文件路径后回车。
        """

        def handle_upload(self, video_path: str, timeout: int = 30) -> bool:
            try:
                import pyautogui
            except ImportError:
                logger.error("未安装 pyautogui，无法操作文件对话框")
                return False
            try:
                time.sleep(2)
                pyautogui.keyDown("ctrl")
                pyautogui.keyDown("l")
                pyautogui.keyUp("l")
                pyautogui.keyUp("ctrl")
                time.sleep(0.3)
                pyautogui.typewrite(video_path, interval=0.01)
                time.sleep(0.5)
                pyautogui.keyDown("return")
                pyautogui.keyUp("return")
                time.sleep(1)
                return True
            except Exception as exc:
                logger.error("文件对话框操作失败：%s", exc)
                return False


@dataclass
class VideoMapping:
    """一行商品视频映射。"""
    pdd_product_id: str   # 拼多多商品ID（用于后台搜索）
    url: str              # 商品链接（仅作记录）
    video_id: str         # 视频来源ID，对应文件名 <video_id>.mp4
    video_path: str       # 视频文件完整路径


class ProductIdMapper:
    """商品映射表读取器。"""

    def __init__(self, mapping_file: str, video_dir: str):
        self.mapping_file = mapping_file
        self.video_dir = video_dir
        self.mappings: List[VideoMapping] = []

    def load_mappings(self) -> List[VideoMapping]:
        """读取映射表，跳过字段不完整或 ID 非数字的行。"""
        if not os.path.exists(self.mapping_file):
            raise FileNotFoundError(f"映射表文件不存在：{self.mapping_file}")

        self.mappings = []
        with open(self.mapping_file, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            f.seek(0)
            delimiter = "\t" if "\t" in first_line else ","
            reader = csv.reader(f, delimiter=delimiter)

            next(reader, None)  # 跳过表头
            for row_num, row in enumerate(reader, start=2):
                if len(row) < 3:
                    logger.warning("第 %d 行字段不完整，跳过：%s", row_num, row)
                    continue
                pdd_id, url, video_id = row[0].strip(), row[1].strip(), row[2].strip()
                if not pdd_id.isdigit() or not video_id.isdigit():
                    logger.warning("第 %d 行 ID 格式不正确，跳过：PDD=%s video=%s",
                                   row_num, pdd_id, video_id)
                    continue
                self.mappings.append(VideoMapping(
                    pdd_product_id=pdd_id,
                    url=url,
                    video_id=video_id,
                    video_path=os.path.join(self.video_dir, f"{video_id}.mp4"),
                ))

        existing = sum(1 for m in self.mappings if os.path.exists(m.video_path))
        logger.info("加载映射 %d 条，其中视频文件存在 %d 条",
                    len(self.mappings), existing)
        return self.mappings

    def get_valid_mappings(self) -> List[VideoMapping]:
        """仅返回视频文件实际存在的映射。"""
        return [m for m in self.mappings if os.path.exists(m.video_path)]


class PDDVideoUploader:
    """拼多多商品讲解视频上传自动化。"""

    def __init__(self, debugger_address: str, mapping_file: str, video_dir: str):
        self.driver = None
        self.wait = None
        self.debugger_address = debugger_address
        self.file_dialog_handler = FileDialogHandler()
        self.id_mapper = ProductIdMapper(mapping_file, video_dir)

    # -- 连接与导航 ---------------------------------------------------------
    def connect_to_chrome(self) -> bool:
        """附着到远程调试模式的 Chrome（不另启浏览器）。"""
        logger.info("连接 Chrome 远程调试端口：%s", self.debugger_address)
        try:
            options = Options()
            # 只能设置 debuggerAddress，附加其他 option 可能导致另起浏览器实例
            options.add_experimental_option("debuggerAddress", self.debugger_address)
            self.driver = webdriver.Chrome(options=options)
            self.wait = WebDriverWait(self.driver, 30)
            logger.info("连接成功，当前页面：%s | %s",
                        self.driver.title, self.driver.current_url)
            return True
        except WebDriverException as exc:
            logger.error("连接 Chrome 失败：%s", exc)
            logger.error("请先以调试模式启动 Chrome，例如：")
            logger.error(r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
                         r'--remote-debugging-port=9222 --user-data-dir="C:\chrome-debug-profile"')
            return False

    def _switch_to_target_window(self) -> bool:
        """切换到「多多视频」窗口。"""
        all_titles = []
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            all_titles.append(self.driver.title)
            if TARGET_WINDOW_TITLE in self.driver.title:
                logger.info("已切换到窗口：%s", self.driver.title)
                return True
        logger.error("未找到标题含 %r 的窗口，当前窗口：%s",
                     TARGET_WINDOW_TITLE, all_titles)
        return False

    def navigate_to_product_explanation(self) -> bool:
        """切换到多多视频窗口并确认已在商品讲解页（不主动跳转，避免打断登录态）。"""
        if not self._switch_to_target_window():
            return False
        current_url = self.driver.current_url
        if "product-explanation" in current_url or "replay-manage" in current_url:
            logger.info("已在商品讲解页面")
            return True
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), '商品讲解')]")
                )
            )
            logger.info("已进入商品讲解页面")
            return True
        except TimeoutException:
            logger.error("未检测到商品讲解页面，请手动打开后重试")
            return False

    # -- 搜索商品 -----------------------------------------------------------
    def search_product(self, product_id: str) -> bool:
        """在商品讲解页按 ID 搜索商品并等待结果出现。"""
        logger.info("搜索商品ID：%s", product_id)
        self._wait_for_loading()
        time.sleep(1)

        id_input = self._find_product_id_input()
        if not id_input:
            logger.error("无法找到商品ID输入框")
            self._debug_print_all_inputs()
            return False

        # 模拟操作 + JS 双保险清空输入框
        self.driver.execute_script("arguments[0].click();", id_input)
        time.sleep(0.3)
        id_input.send_keys(Keys.CONTROL, "a")
        id_input.send_keys(Keys.DELETE)
        self.driver.execute_script(
            """
            var elem = arguments[0];
            elem.value = '';
            elem.dispatchEvent(new Event('input', {bubbles: true}));
            elem.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            id_input,
        )
        time.sleep(0.3)
        id_input.send_keys(str(product_id))
        time.sleep(0.5)

        search_btn = self._find_search_button()
        if not search_btn:
            logger.error("未找到查询按钮")
            self._debug_print_all_buttons()
            return False
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", search_btn
        )
        time.sleep(0.3)
        search_btn.click()
        logger.info("已点击查询按钮，等待结果加载...")
        self._wait_for_loading()

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located(
                    (By.XPATH, f"//*[contains(text(), '{product_id}')]")
                )
            )
            logger.info("商品 %s 搜索成功", product_id)
            return True
        except TimeoutException:
            logger.warning("查询结果中未找到商品 %s", product_id)
            return False

    def _find_product_id_input(self):
        """多策略定位商品 ID 输入框。"""
        try:
            return self.wait.until(
                EC.presence_of_element_located(LOCATORS["product_id_input"])
            )
        except TimeoutException:
            pass
        # 策略2：遍历全部 input 检查 placeholder
        for inp in self.driver.find_elements(By.TAG_NAME, "input"):
            placeholder = inp.get_attribute("placeholder") or ""
            if "查询" in placeholder or "商品" in placeholder:
                return inp
        # 策略3：Element UI 输入框
        try:
            return self.driver.find_element(
                By.CSS_SELECTOR, "input.el-input__inner[placeholder*='查询']"
            )
        except NoSuchElementException:
            return None

    def _find_search_button(self):
        """多策略定位「查询」按钮。"""
        for btn in self.driver.find_elements(
            By.CSS_SELECTOR, "button[data-testid='beast-core-button']"
        ):
            if "查询" in btn.text.strip():
                return btn
        for btn in self.driver.find_elements(By.TAG_NAME, "button"):
            if "查询" in btn.text.strip():
                return btn
        return None

    # -- 上传视频 -----------------------------------------------------------
    def _find_upload_button(self, product_id: str):
        """多策略定位指定商品行内的「上传讲解」按钮。"""
        try:
            product_row = self.driver.find_element(
                By.XPATH, f"//tr[contains(., '{product_id}')]"
            )
            return product_row.find_element(*LOCATORS["upload_button"])
        except NoSuchElementException:
            pass
        for btn in self.driver.find_elements(
            By.CSS_SELECTOR, "button[data-testid='beast-core-button']"
        ):
            if "上传讲解" in btn.text.strip():
                return btn
        for btn in self.driver.find_elements(By.TAG_NAME, "button"):
            if "上传讲解" in btn.text.strip():
                return btn
        logger.error("未找到上传讲解按钮")
        self._debug_print_all_buttons()
        return None

    def upload_video_for_product(self, mapping: VideoMapping) -> dict:
        """为单个商品执行完整上传流程，返回结果字典。"""
        result = {
            "pdd_product_id": mapping.pdd_product_id,
            "video_id": mapping.video_id,
            "video_path": mapping.video_path,
            "success": False,
            "message": "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not os.path.exists(mapping.video_path):
            result["message"] = f"视频文件不存在：{mapping.video_path}"
            logger.error(result["message"])
            return result

        size_mb = os.path.getsize(mapping.video_path) / (1024 * 1024)
        logger.info("视频大小：%.2f MB", size_mb)

        if not self.search_product(mapping.pdd_product_id):
            result["message"] = f"未找到商品 {mapping.pdd_product_id}"
            return result

        upload_btn = self._find_upload_button(mapping.pdd_product_id)
        if not upload_btn:
            result["message"] = "未找到上传讲解按钮"
            return result

        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
            upload_btn,
        )
        time.sleep(0.5)
        upload_btn.click()
        logger.info("已点击上传讲解，等待系统文件对话框...")
        time.sleep(1.5)

        if not self.file_dialog_handler.handle_upload(mapping.video_path):
            result["message"] = "Windows 文件对话框操作失败"
            return result

        modal = self._handle_success_modal()
        result["success"] = modal["success"]
        result["message"] = modal["message"]
        return result

    def _handle_success_modal(self, timeout: int = UPLOAD_TIMEOUT_SECONDS) -> dict:
        """等待上传成功弹窗并通过多种兜底策略关闭。"""
        result = {"success": False, "message": ""}
        try:
            success_modal = self.wait.until(
                EC.visibility_of_element_located(LOCATORS["success_modal"])
            )
            logger.info("检测到「视频上传成功」弹窗")
            clicked = self._close_success_modal(success_modal)
            if not clicked:
                # 最后兜底：ESC 关闭
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(1)
            result["success"] = True
            result["message"] = "视频上传成功，请耐心等待审核"
        except TimeoutException:
            result["message"] = "等待上传成功弹窗超时"
        except Exception as exc:
            result["message"] = f"处理成功弹窗异常：{exc}"
            logger.error(result["message"])
        return result

    def _close_success_modal(self, success_modal) -> bool:
        """依次尝试多种选择器点击弹窗关闭按钮。"""
        attempts = [
            ("class*='useUpload_closeBtn'", lambda: self.wait.until(
                EC.element_to_be_clickable(LOCATORS["close_modal_button"])).click()),
            ("弹窗内 closeBtn", lambda: success_modal.find_element(
                By.CSS_SELECTOR, "div[class*='closeBtn']").click()),
            ("弹窗内 close/Close", lambda: success_modal.find_element(
                By.CSS_SELECTOR, "div[class*='close'], div[class*='Close']").click()),
        ]
        for desc, action in attempts:
            try:
                action()
                logger.info("已关闭成功弹窗（%s）", desc)
                return True
            except (TimeoutException, NoSuchElementException):
                continue
        # 策略4：JS 兜底
        try:
            self.driver.execute_script("""
                var btn = document.querySelector('div[class*="useUpload_closeBtn"]');
                if (btn) { btn.click(); return; }
                var modal = document.querySelector('div[class*="MDL_modal"]');
                if (modal) {
                    modal.querySelectorAll('div').forEach(function (d) {
                        var cls = d.className || '';
                        if (typeof cls === 'object') cls = cls.baseVal || '';
                        if (cls.indexOf('close') !== -1 || cls.indexOf('Close') !== -1) {
                            d.click();
                        }
                    });
                }
            """)
            logger.info("已通过 JavaScript 关闭弹窗")
            return True
        except Exception as exc:
            logger.warning("JS 关闭弹窗失败：%s", exc)
            return False

    # -- 批量执行 -----------------------------------------------------------
    def batch_upload(self, max_items: Optional[int] = None) -> List[dict]:
        """批量上传，返回每个商品的结果列表。"""
        self.id_mapper.load_mappings()
        valid_mappings = self.id_mapper.get_valid_mappings()
        if not valid_mappings:
            logger.error("没有可上传的有效视频文件")
            return []
        if max_items:
            valid_mappings = valid_mappings[:max_items]

        results = []
        total = len(valid_mappings)
        logger.info("开始批量上传，共 %d 个商品", total)
        for index, mapping in enumerate(valid_mappings, start=1):
            logger.info("===== %d/%d  PDD=%s video=%s =====",
                        index, total, mapping.pdd_product_id, mapping.video_id)
            results.append(self.upload_video_for_product(mapping))
            success_count = sum(1 for r in results if r["success"])
            logger.info("进度 %d/%d，成功 %d", index, total, success_count)
            if index < total:
                time.sleep(ITEM_INTERVAL_SECONDS)

        success_count = sum(1 for r in results if r["success"])
        logger.info("批量上传完成：成功 %d/%d", success_count, total)
        return results

    # -- 调试辅助 -----------------------------------------------------------
    def _wait_for_loading(self, timeout: int = 30) -> None:
        try:
            loading = self.driver.find_element(*LOCATORS["loading"])
            WebDriverWait(self.driver, timeout).until(
                EC.invisibility_of_element(loading)
            )
        except NoSuchElementException:
            pass

    def _debug_print_all_inputs(self) -> None:
        for i, inp in enumerate(self.driver.find_elements(By.TAG_NAME, "input")):
            logger.info("input[%d] placeholder=%r class=%r displayed=%s",
                        i, inp.get_attribute("placeholder"),
                        inp.get_attribute("class"), inp.is_displayed())

    def _debug_print_all_buttons(self) -> None:
        for i, btn in enumerate(self.driver.find_elements(By.TAG_NAME, "button")):
            logger.info("button[%d] text=%r testid=%r",
                        i, btn.text.strip(), btn.get_attribute("data-testid"))


def setup_logging(verbose: bool = False) -> None:
    ensure_data_dirs()
    log_file = LOG_DIR / "pdd_video_upload.log"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="拼多多商品讲解视频批量上传")
    parser.add_argument(
        "-m", "--mapping",
        default=str(DATA_DIR / "product_mapping.csv"),
        help="商品视频映射表 CSV 路径，默认 data/product_mapping.csv",
    )
    parser.add_argument(
        "-d", "--video-dir", default=str(VIDEO_DIR),
        help="视频文件目录，默认 data/videos",
    )
    parser.add_argument(
        "--debugger-address", default=CHROME_DEBUG_ADDRESS,
        help=f"Chrome 远程调试地址，默认 {CHROME_DEBUG_ADDRESS}",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅上传前 N 个商品")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    logger.info("拼多多商品讲解视频批量上传")
    logger.info("前置确认：1) Chrome 调试模式已启动；2) 已登录商家后台并打开商品讲解页；"
                "3) 映射表与视频文件就绪")

    uploader = PDDVideoUploader(
        debugger_address=args.debugger_address,
        mapping_file=args.mapping,
        video_dir=args.video_dir,
    )
    if not uploader.connect_to_chrome():
        return 1
    if not uploader.navigate_to_product_explanation():
        return 1

    results = uploader.batch_upload(max_items=args.limit)
    if results:
        success = sum(1 for r in results if r["success"])
        logger.info("上传结果汇总：成功 %d / 失败 %d / 总计 %d",
                    success, len(results) - success, len(results))
        for result in results:
            status = "成功" if result["success"] else "失败"
            logger.info("  PDD=%s video=%s [%s] %s",
                        result["pdd_product_id"], result["video_id"],
                        status, result["message"])
    logger.info("操作完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
