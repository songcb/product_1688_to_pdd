# -*- coding: utf-8 -*-
"""微信拼多多小程序「以图搜货」比价。

对 ``product_1688`` 表中的每个 1688 商品：
    1. 从 ``img_1688`` 表取出主图（01.jpg）落地为临时文件；
    2. 通过 Windows UI 自动化操作 PC 微信中的拼多多小程序，
       以「上传图片搜索」方式检索同款商品；
    3. 截取搜索结果，用 pHash / SSIM / ORB 三种算法与原图做相似度比对；
    4. 统计全部搜索结果与相似商品的最低价、最高价、平均价，
       写入 ``product_pdd_compare`` 与 ``pdd_search_item`` 表。

注意（仅支持 Windows）：
    - 依赖 PC 版微信、uiautomation、pyautogui；
    - 下方 *_XY 坐标基于固定尺寸的小程序窗口（约 540x960）实测，
      若微信版本或窗口尺寸不同，需要用 insdk/uiautomation 工具重新标定；
    - 运行前需手动打开微信中的拼多多小程序并停留在首页。
"""
from __future__ import annotations

import argparse
import logging
import os
import time
import warnings
from datetime import datetime

try:  # cryptography 为间接依赖，未安装时无需告警
    from cryptography.utils import CryptographyDeprecationWarning
    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except Exception:
    pass

import cv2
import imagehash
import numpy as np
import pymysql
import pyautogui
import pyperclip
import uiautomation as auto
from PIL import Image
from skimage.metrics import structural_similarity as ssim

from config import PDD_RESULT_DIR, SEARCH_PIC_DIR, ensure_data_dirs
from db import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 相似度判定阈值（针对商品宣传图调参，任一满足即视为同款）
# ---------------------------------------------------------------------------
PHASH_THRESHOLD = 0.90    # pHash 相似度 > 0.90 基本同源
SSIM_THRESHOLD = 0.85     # SSIM > 0.85 非常相似
ORB_THRESHOLD = 0.15      # ORB 特征匹配比例 > 0.15 可能同源

# ---------------------------------------------------------------------------
# 拼多多小程序 UI 坐标（像素，窗口尺寸变化时需重新标定）
# ---------------------------------------------------------------------------
CAMERA_BUTTON_XY = (256, 52)        # 搜索框相机图标
ALBUM_BUTTON_XY = (409, 490)        # 「从相册选择」
FILE_INPUT_XY = (286, 238)          # 系统文件对话框的文件名输入框
FILE_CONFIRM_XY = (454, 269)        # 系统文件对话框的「打开」按钮
GOODS_LIST_HOVER_XY = (520, 690)    # 商品列表区域（滚动前把鼠标移入）
BACK_BUTTON_XY = (437, 42)          # 左上角返回按钮
SCREEN_MAX_BOTTOM = 960             # 商品图截图区域下边界，超出视为不可见

# ---------------------------------------------------------------------------
# uiautomation 控件树路径（微信版本升级后可能变化）
# ---------------------------------------------------------------------------
GOODS_LIST_PATH = (
    "DocumentControl[0]->GroupControl[1]->GroupControl[1]->GroupControl[1]->"
    "GroupControl[0]->PaneControl[1]->DocumentControl[0]->GroupControl[0]->"
    "GroupControl[1]->GroupControl[2]->GroupControl[0]->GroupControl[0]->"
    "GroupControl[0]"
)
ITEM_IMAGE_PATH = "GroupControl[0]->GroupControl[0]->ImageControl[0]"
ITEM_TITLE_PATH = "GroupControl[1]"
ITEM_PRICE_PATH = "GroupControl[3]"

# 搜索结果加载重试次数与间隔
LIST_FIND_RETRIES = 10
LIST_FIND_INTERVAL = 3


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
def init_tables() -> bool:
    """创建比价结果表与搜索结果明细表。"""
    create_compare_table = """
    CREATE TABLE IF NOT EXISTS product_pdd_compare (
        `宝贝ID` VARCHAR(50) PRIMARY KEY,
        `公司名称` VARCHAR(255) NOT NULL,
        `宝贝链接` VARCHAR(500) NOT NULL,
        `上架时间` DATETIME,
        `类目` VARCHAR(255),
        `title` VARCHAR(500),
        `min_price` DECIMAL(10,2),
        `max_price` DECIMAL(10,2),
        `sim_count` INT DEFAULT 0,
        `sim_min_price` DECIMAL(10,2),
        `sim_max_price` DECIMAL(10,2),
        `search_min_price` DECIMAL(10,2),
        `search_avg_price` DECIMAL(10,2),
        `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    create_item_table = """
    CREATE TABLE IF NOT EXISTS pdd_search_item (
        `公司名称` VARCHAR(255) NOT NULL,
        `宝贝ID` VARCHAR(50) NOT NULL,
        `search_idx` INT,
        `title` VARCHAR(255) NOT NULL,
        `price_list` VARCHAR(255) NOT NULL,
        `min_price` DECIMAL(10,2),
        `is_ssim` BOOLEAN,
        `img_data` LONGBLOB NOT NULL,
        `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`公司名称`, `宝贝ID`, `search_idx`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(create_compare_table)
            cursor.execute(create_item_table)
        conn.commit()
        conn.close()
        logger.info("数据库表初始化完成")
        return True
    except Exception as exc:
        logger.error("初始化数据库表失败：%s", exc)
        return False


def get_products_from_db(company_name: str) -> list:
    """读取 product_1688 表中指定店铺的商品（按上架时间倒序）。"""
    sql = """
        SELECT * FROM product_1688
        WHERE `公司名称` = %s
        ORDER BY `上架时间` DESC;
    """
    try:
        conn = get_connection()
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (company_name,))
            products = cursor.fetchall()
        conn.close()
        logger.info("从数据库读取到 %d 条商品数据", len(products))
        return products
    except Exception as exc:
        logger.error("读取商品数据失败：%s", exc)
        return []


def get_main_image_from_db(company_name: str, item_id: str):
    """读取商品主图 01.jpg 的二进制内容，不存在返回 None（参数化查询）。"""
    sql = (
        "SELECT img_data FROM img_1688 "
        "WHERE `公司名称` = %s AND `宝贝ID` = %s AND `图片名称` = '01.jpg'"
    )
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, (company_name, item_id))
            row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        logger.error("读取商品主图失败：%s", exc)
        return None


def insert_compare_record(data: dict) -> bool:
    """upsert 一条商品比价汇总记录到 product_pdd_compare。"""
    sql = """
        INSERT INTO product_pdd_compare
        (`宝贝ID`, `公司名称`, `宝贝链接`, `上架时间`, `类目`, `title`,
         `min_price`, `max_price`, `sim_count`, `sim_min_price`,
         `sim_max_price`, `search_min_price`, `search_avg_price`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `公司名称` = VALUES(`公司名称`),
            `宝贝链接` = VALUES(`宝贝链接`),
            `上架时间` = VALUES(`上架时间`),
            `类目` = VALUES(`类目`),
            `title` = VALUES(`title`),
            `min_price` = VALUES(`min_price`),
            `max_price` = VALUES(`max_price`),
            `sim_count` = VALUES(`sim_count`),
            `sim_min_price` = VALUES(`sim_min_price`),
            `sim_max_price` = VALUES(`sim_max_price`),
            `search_min_price` = VALUES(`search_min_price`),
            `search_avg_price` = VALUES(`search_avg_price`)
    """
    params = (
        data["item_id"], data["company_name"], data["product_url"],
        data["up_time"], data["category"], data["title"],
        data["min_price"], data["max_price"], data["sim_count"],
        data["sim_min_price"], data["sim_max_price"],
        data["search_min_price"], data["search_avg_price"],
    )
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("写入比价汇总失败（%s）：%s", data.get("item_id"), exc)
        return False


def insert_search_item(data: dict) -> bool:
    """upsert 一条拼多多搜索结果明细到 pdd_search_item。"""
    sql = """
        INSERT INTO pdd_search_item
        (`公司名称`, `宝贝ID`, `search_idx`, `title`, `price_list`,
         `min_price`, `is_ssim`, `img_data`)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            `title` = VALUES(`title`),
            `price_list` = VALUES(`price_list`),
            `min_price` = VALUES(`min_price`),
            `is_ssim` = VALUES(`is_ssim`),
            `img_data` = VALUES(`img_data`)
    """
    params = (
        data["company_name"], data["item_id"], data["search_idx"],
        data["title"], data["price_list"], data["min_price"],
        data["is_ssim"], data["img_data"],
    )
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("写入搜索明细失败（%s-%s）：%s",
                     data.get("item_id"), data.get("search_idx"), exc)
        return False


# ---------------------------------------------------------------------------
# 图片相似度
# ---------------------------------------------------------------------------
def cv_imread(file_path: str):
    """读取含中文路径的图片（cv2.imread 不支持非 ASCII 路径）。"""
    return cv2.imdecode(np.fromfile(file_path, dtype=np.uint8), cv2.IMREAD_COLOR)


def phash_similarity(img1_path: str, img2_path: str) -> float:
    """pHash 感知哈希相似度，范围 0~1。"""
    hash1 = imagehash.phash(Image.open(img1_path))
    hash2 = imagehash.phash(Image.open(img2_path))
    return 1 - (hash1 - hash2) / 64


def ssim_similarity(img1_path: str, img2_path: str) -> float:
    """SSIM 结构相似度，统一尺寸与灰度后比较。"""
    img1 = cv2.cvtColor(cv_imread(img1_path), cv2.COLOR_BGR2GRAY)
    img2 = cv2.cvtColor(cv_imread(img2_path), cv2.COLOR_BGR2GRAY)
    img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    score, _ = ssim(img1, img2, full=True)
    return score


def orb_similarity(img1_path: str, img2_path: str) -> float:
    """ORB 特征匹配比例：好匹配数占关键点总数的比例。"""
    gray1 = cv2.cvtColor(cv_imread(img1_path), cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(cv_imread(img2_path), cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(2000)
    kp1, des1 = orb.detectAndCompute(gray1, None)
    kp2, des2 = orb.detectAndCompute(gray2, None)
    if des1 is None or des2 is None:
        return 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(des1, des2)
    good_matches = [m for m in matches if m.distance < 50]
    return len(good_matches) / max(len(kp1), len(kp2))


def is_img_similarity(img1_path: str, img2_path: str) -> bool:
    """综合 pHash/SSIM/ORB 三种算法判断两张商品图是否同款。"""
    if not os.path.exists(img1_path):
        logger.warning("图片不存在：%s", img1_path)
        return False
    if not os.path.exists(img2_path):
        logger.warning("图片不存在：%s", img2_path)
        return False
    try:
        return (
            phash_similarity(img1_path, img2_path) > PHASH_THRESHOLD
            or ssim_similarity(img1_path, img2_path) > SSIM_THRESHOLD
            or orb_similarity(img1_path, img2_path) > ORB_THRESHOLD
        )
    except Exception as exc:
        logger.warning("图片相似度计算失败：%s", exc)
        return False


# ---------------------------------------------------------------------------
# 微信小程序 UI 自动化
# ---------------------------------------------------------------------------
def find_pdd_window():
    """在桌面窗口中查找标题含「拼多多」的窗口。"""
    logger.info("正在查找拼多多窗口...")
    for window in auto.GetRootControl().GetChildren():
        if window.Name and "拼多多" in window.Name:
            logger.info("找到拼多多窗口：%s", window.Name)
            return window
    logger.error("未找到拼多多窗口，请先在微信中打开拼多多小程序")
    return None


def find_element_by_path(window, path: str):
    """按 ``类型[索引]->类型[索引]`` 的控件路径从根窗口定位控件。"""
    current = window
    for part in [p for p in path.replace("/", "->").split("->") if p]:
        if "[" not in part or "]" not in part:
            logger.warning("路径片段格式错误：%s", part)
            return None
        control_type, index_str = part.split("[", 1)
        index = int(index_str.rstrip("]"))
        children = current.GetChildren()
        if index >= len(children):
            logger.warning("索引 %d 超出范围（共 %d 个子元素）", index, len(children))
            return None
        if children[index].ControlTypeName != control_type:
            logger.warning("控件类型不匹配：期望 %s，实际 %s",
                           control_type, children[index].ControlTypeName)
            return None
        current = children[index]
    return current


def _resolve_sub_path(parent, path: str):
    """在父控件下按相对路径定位子控件，失败返回 None。"""
    current = parent
    for part in [p for p in path.replace("/", "->").split("->") if p]:
        if "[" not in part or "]" not in part:
            return None
        control_type, index_str = part.split("[", 1)
        index = int(index_str.rstrip("]"))
        children = current.GetChildren()
        if index >= len(children) or children[index].ControlTypeName != control_type:
            return None
        current = children[index]
    return current


def collect_text_by_path(parent, path: str) -> list:
    """定位子控件后，深度收集其下全部 TextControl 的文本。"""
    target = _resolve_sub_path(parent, path)
    if not target:
        return []

    def traverse(element):
        texts = []
        if element.ControlTypeName == "TextControl" and element.Name:
            texts.append(element.Name)
        for child in element.GetChildren():
            texts.extend(traverse(child))
        return texts

    return traverse(target)


def save_img_to_file(element, filepath: str) -> bool:
    """对单个商品的 ImageControl 做区域截图（要求控件位于屏幕可见区）。"""
    if not element or element.ControlTypeName != "ImageControl":
        return False
    try:
        rect = element.BoundingRectangle
        if not rect or rect.left >= rect.right or rect.top >= rect.bottom:
            logger.warning("商品图边界矩形无效，可能未渲染")
            return False
        if rect.bottom >= SCREEN_MAX_BOTTOM:
            logger.warning("商品图超出可见区域，需要滚动后重试")
            return False
        pyautogui.screenshot(
            region=(rect.left, rect.top, rect.right - rect.left,
                    rect.bottom - rect.top)
        ).save(filepath)
        return True
    except Exception as exc:
        logger.warning("商品图截图失败：%s", exc)
        return False


def save_list_img_to_file(element, filepath: str) -> bool:
    """对整个商品搜索结果列表做区域截图。"""
    if not element:
        return False
    try:
        rect = element.BoundingRectangle
        if not rect or rect.left >= rect.right or rect.top >= rect.bottom:
            logger.warning("商品列表边界矩形无效")
            return False
        pyautogui.screenshot(
            region=(rect.left, rect.top, rect.right - rect.left,
                    rect.bottom - rect.top)
        ).save(filepath)
        logger.info("商品列表截图已保存：%s", filepath)
        return True
    except Exception as exc:
        logger.warning("商品列表截图失败：%s", exc)
        return False


def parse_price(price_texts: list) -> float:
    """从控件文本数组中提取价格：取所有 '¥' 后面数值的最小值。"""
    prices = []
    for idx in range(len(price_texts) - 1):
        if price_texts[idx] == "¥":
            try:
                prices.append(float(price_texts[idx + 1]))
            except ValueError:
                continue
    return min(prices) if prices else 0.0


def _input_search_image(search_pic_path: str) -> None:
    """在拼多多小程序中点击相机→相册，并在文件对话框填入图片路径。"""
    auto.MoveTo(*CAMERA_BUTTON_XY)
    auto.Click(*CAMERA_BUTTON_XY)
    time.sleep(0.5)

    auto.MoveTo(*ALBUM_BUTTON_XY)
    auto.Click(*ALBUM_BUTTON_XY)
    time.sleep(1)

    # 系统文件对话框不能直接输入中文路径，借助剪贴板粘贴
    pyautogui.moveTo(*FILE_INPUT_XY, duration=0.3)
    pyautogui.click(button="left")
    time.sleep(0.2)
    pyperclip.copy(search_pic_path)
    pyautogui.hotkey("ctrl", "v")
    logger.info("已输入搜索图片路径：%s", search_pic_path)

    auto.MoveTo(*FILE_CONFIRM_XY)
    auto.Click(*FILE_CONFIRM_XY)
    time.sleep(3)


def get_one_goods_data(pdd_window, search_pic_path: str, save_dir: str) -> list:
    """用一张商品图在拼多多小程序中搜索，采集结果列表的图片、标题与价格。"""
    os.makedirs(save_dir, exist_ok=True)
    _input_search_image(search_pic_path)

    # 等待商品列表控件出现
    goods_list = None
    for _ in range(LIST_FIND_RETRIES):
        goods_list = find_element_by_path(pdd_window, GOODS_LIST_PATH)
        if goods_list is not None:
            break
        time.sleep(LIST_FIND_INTERVAL)
    if goods_list is None:
        logger.error("未找到商品结果列表控件")
        return []

    auto.MoveTo(*GOODS_LIST_HOVER_XY)
    save_list_img_to_file(goods_list, os.path.join(save_dir, "list_cap.png"))

    goods_info_list = []
    for search_idx, item_control in enumerate(goods_list.GetChildren(), start=1):
        logger.info("----------- 搜索结果 %d -----------", search_idx)
        img_path = os.path.join(save_dir, f"{search_idx:02d}.png")

        # 截图商品图，不可见时滚动一次后重试
        img_element = _resolve_sub_path(item_control, ITEM_IMAGE_PATH)
        img_saved = False
        if img_element:
            for _ in range(3):
                if save_img_to_file(img_element, img_path):
                    img_saved = True
                    break
                auto.WheelDown(2)
        if not img_saved:
            continue

        title_list = collect_text_by_path(item_control, ITEM_TITLE_PATH)
        price_texts = collect_text_by_path(item_control, ITEM_PRICE_PATH)
        min_price = parse_price(price_texts)

        with open(img_path, "rb") as f:
            img_data = f.read()
        is_similar = is_img_similarity(search_pic_path, img_path)

        goods_info_list.append({
            "search_idx": search_idx,
            "title": "".join(title_list),
            "price_list": " ".join(price_texts),
            "min_price": min_price,
            "is_ssim": is_similar,
            "img_data": img_data,
        })

    # 返回上一页，保证下一次搜索可用
    auto.MoveTo(*BACK_BUTTON_XY)
    auto.Click(*BACK_BUTTON_XY)
    return goods_info_list


# ---------------------------------------------------------------------------
# 比价主流程
# ---------------------------------------------------------------------------
def do_price_compare(pdd_window, company_name: str,
                     search_pic_dir: str, result_dir: str) -> None:
    """对一个店铺的全部商品执行以图搜货并汇总比价结果入库。"""
    products = get_products_from_db(company_name)
    for current_idx, goods in enumerate(products, start=1):
        item_id = goods["宝贝ID"]
        title = goods["title"]
        min_price = float(goods["min_price"] or 0)
        max_price = float(goods["max_price"] or 0)
        logger.info("%d/%d item_id=%s title=%s 1688最低价=%.2f",
                    current_idx, len(products), item_id, title, min_price)

        # 主图落地为临时文件
        img_data = get_main_image_from_db(company_name, item_id)
        if img_data is None:
            logger.warning("商品 %s 无主图，跳过", item_id)
            continue
        search_pic_path = os.path.join(search_pic_dir, f"{item_id}_01.jpg")
        with open(search_pic_path, "wb") as f:
            f.write(img_data)

        # 统计量初始化
        sim_count = 0
        sim_min_price = float("inf")
        sim_max_price = float("-inf")
        search_min_price = float("inf")
        search_total_price = 0.0
        search_count = 0

        search_results = get_one_goods_data(
            pdd_window, search_pic_path, os.path.join(result_dir, str(item_id))
        )
        search_count = len(search_results)
        for item in search_results:
            insert_search_item({
                "company_name": company_name,
                "item_id": item_id,
                "search_idx": item["search_idx"],
                "title": item["title"],
                "price_list": item["price_list"],
                "min_price": item["min_price"],
                "is_ssim": item["is_ssim"],
                "img_data": item["img_data"],
            })

            price = item["min_price"]
            search_min_price = min(search_min_price, price)
            search_total_price += price
            if item["is_ssim"]:
                sim_count += 1
                sim_min_price = min(sim_min_price, price)
                sim_max_price = max(sim_max_price, price)

        search_avg_price = (
            search_total_price / search_count if search_count else 0.0
        )
        if sim_count == 0:
            sim_min_price = sim_max_price = 0.0

        insert_compare_record({
            "item_id": item_id,
            "company_name": company_name,
            "product_url": goods["宝贝链接"],
            "up_time": goods["上架时间"],
            "category": goods["类目"],
            "title": title,
            "min_price": min_price,
            "max_price": max_price,
            "sim_count": sim_count,
            "sim_min_price": sim_min_price,
            "sim_max_price": sim_max_price,
            "search_min_price": search_min_price if search_count else 0.0,
            "search_avg_price": search_avg_price,
        })

        logger.info(
            "1688价格=%.2f~%.2f | 同款数=%d 同款价=%.2f~%.2f | "
            "搜索最低价=%.2f 搜索均价=%.2f",
            min_price, max_price, sim_count, sim_min_price, sim_max_price,
            search_min_price if search_count else 0.0, search_avg_price,
        )
        logger.info("商品完成时间：%s",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="微信拼多多小程序以图搜货比价（仅 Windows）"
    )
    parser.add_argument("company_name", help="公司/店铺名称（须与库中一致）")
    parser.add_argument("--pic-dir", default=None, help="搜索用临时图片目录")
    parser.add_argument("--result-dir", default=None, help="搜索结果截图目录")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ensure_data_dirs()

    search_pic_dir = args.pic_dir or str(SEARCH_PIC_DIR / args.company_name)
    result_dir = args.result_dir or str(PDD_RESULT_DIR / args.company_name)
    os.makedirs(search_pic_dir, exist_ok=True)
    os.makedirs(result_dir, exist_ok=True)

    init_tables()
    pdd_window = find_pdd_window()
    if not pdd_window:
        return
    do_price_compare(pdd_window, args.company_name, search_pic_dir, result_dir)
    logger.info("全部完成")


if __name__ == "__main__":
    main()
