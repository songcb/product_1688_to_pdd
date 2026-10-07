# -*- coding: utf-8 -*-
"""1688 商品详情与图片采集。

数据流程：
    1. 从 MySQL 的 ``product`` 表（由 1688 官方采购助手导出的 Excel 导入）
       读取指定店铺的商品列表；
    2. 逐个打开商品详情页，解析标题、阶梯价、SKU 属性与主图 URL，
       结构化信息写入 ``product_1688`` 表；
    3. 通过 Canvas 截图方式下载主图二进制，写入 ``img_1688`` 表。

前置条件：
    1. MySQL 已按 sql/schema.sql 建表，且 product 表已导入商品数据；
    2. Chrome 已以远程调试模式启动并登录 1688。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import logging
import os
import random
import time

import pymysql
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from browser import connect_chrome
from db import get_connection

logger = logging.getLogger(__name__)

# 业务过滤项：按自身经营类目调整，空集合表示不过滤任何类目
SKIP_CATEGORIES = {"胸针"}
# 仅处理最近 N 天上架的商品；None 表示不按上架时间过滤
MAX_PRODUCT_AGE_DAYS = None
# 详情页解析失败重试次数
MAX_RETRIES = 3
# 小于该大小（字节）的图片视为下载失败
MIN_IMAGE_SIZE = 1024


def init_tables() -> bool:
    """创建商品详情表与图片表（已存在则跳过）。"""
    create_product_table = """
    CREATE TABLE IF NOT EXISTS product_1688 (
        `宝贝ID` VARCHAR(50) PRIMARY KEY,
        `公司名称` VARCHAR(255) NOT NULL,
        `宝贝链接` VARCHAR(500) NOT NULL,
        `上架时间` DATETIME,
        `类目` VARCHAR(255),
        `title` VARCHAR(500),
        `min_price` DECIMAL(10,2),
        `max_price` DECIMAL(10,2),
        `img_list` TEXT,
        `expand_view_list` TEXT,
        `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    create_img_table = """
    CREATE TABLE IF NOT EXISTS img_1688 (
        `公司名称` VARCHAR(255) NOT NULL,
        `宝贝ID` VARCHAR(50) NOT NULL,
        `图片名称` VARCHAR(50) NOT NULL,
        `img_data` LONGBLOB NOT NULL,
        `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`公司名称`, `宝贝ID`, `图片名称`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(create_product_table)
            cursor.execute(create_img_table)
        conn.commit()
        conn.close()
        logger.info("数据库表初始化完成")
        return True
    except Exception as exc:
        logger.error("初始化数据库表失败：%s", exc)
        return False


def get_products_from_db(company_name: str) -> list:
    """读取 product 表中指定店铺的全部商品（按上架时间倒序）。"""
    sql = """
        SELECT * FROM product
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


def upsert_product(product: dict) -> bool:
    """新增或更新一条商品详情记录。"""
    check_sql = "SELECT 1 FROM product_1688 WHERE `宝贝ID` = %s"
    update_sql = """
        UPDATE product_1688 SET
            `公司名称` = %s, `宝贝链接` = %s, `上架时间` = %s, `类目` = %s,
            `title` = %s, `min_price` = %s, `max_price` = %s,
            `img_list` = %s, `expand_view_list` = %s
        WHERE `宝贝ID` = %s
    """
    insert_sql = """
        INSERT INTO product_1688 (
            `宝贝ID`, `公司名称`, `宝贝链接`, `上架时间`, `类目`,
            `title`, `min_price`, `max_price`, `img_list`, `expand_view_list`
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        product["公司名称"], product["宝贝链接"], product["上架时间"], product["类目"],
        product.get("title", ""), product.get("min_price", 0), product.get("max_price", 0),
        json.dumps(product.get("img_list", []), ensure_ascii=False),
        json.dumps(product.get("expand_view_list", []), ensure_ascii=False),
    )
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(check_sql, (product["宝贝ID"],))
            if cursor.fetchone():
                cursor.execute(update_sql, params + (product["宝贝ID"],))
            else:
                cursor.execute(insert_sql, (product["宝贝ID"],) + params)
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("保存商品 %s 失败：%s", product.get("宝贝ID"), exc)
        return False


def upsert_image(company_name: str, item_id: str, img_name: str, img_data: bytes) -> bool:
    """新增或更新一张商品图片。"""
    check_sql = (
        "SELECT 1 FROM img_1688 "
        "WHERE `公司名称` = %s AND `宝贝ID` = %s AND `图片名称` = %s"
    )
    update_sql = (
        "UPDATE img_1688 SET `img_data` = %s "
        "WHERE `公司名称` = %s AND `宝贝ID` = %s AND `图片名称` = %s"
    )
    insert_sql = (
        "INSERT INTO img_1688 (`公司名称`, `宝贝ID`, `图片名称`, `img_data`) "
        "VALUES (%s, %s, %s, %s)"
    )
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(check_sql, (company_name, item_id, img_name))
            if cursor.fetchone():
                cursor.execute(update_sql, (img_data, company_name, item_id, img_name))
            else:
                cursor.execute(insert_sql, (company_name, item_id, img_name, img_data))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error("保存图片 %s/%s/%s 失败：%s", company_name, item_id, img_name, exc)
        return False


def parse_detail_html(soup: BeautifulSoup) -> dict:
    """解析商品详情页：标题、价格区间、SKU 属性、主图 URL 列表。"""
    # 主图优先取 img-list-wrapper；部分页面改在 od-scroller-list 的背景图中
    img_list = []
    gallery = soup.select_one("div.img-list-wrapper")
    if gallery:
        for child in gallery.find_all("div", recursive=False):
            img = child.select_one("img")
            if img and img.get("src") and not img["src"].endswith(".png"):
                img_list.append(img["src"])
    else:
        logger.info("未找到 img-list-wrapper，尝试从 od-scroller-list 背景图提取")
        scroller = soup.select_one("div.od-scroller-list")
        if scroller:
            seen = set()
            for span in scroller.find_all("span"):
                style = span.get("style", "")
                if "url(" not in style or "http" not in style:
                    continue
                img_url = style.split("url(")[1].split(")")[0].strip('"')
                if img_url not in seen:
                    seen.add(img_url)
                    img_list.append(img_url)

    title = ""
    title_node = soup.select_one("div.title-content")
    if title_node:
        title = title_node.text.strip()

    min_price = max_price = 0.0
    for price_node in soup.find_all("div", class_="price-info"):
        try:
            price = float(price_node.text.strip().replace("¥", "").strip())
        except ValueError:
            continue
        min_price = price if min_price == 0 else min(min_price, price)
        max_price = max(max_price, price)

    expand_view_list = []
    sku_box = soup.select_one("div.expand-view-list")
    if sku_box:
        for item in sku_box.find_all("div", class_="expand-view-item"):
            name_node = item.select_one("div.v-flex")
            price_node = item.select_one("span.item-price-stock")
            if name_node and price_node:
                expand_view_list.append({
                    "name": name_node.text.strip(),
                    "price": price_node.text.strip(),
                })

    return {
        "title": title,
        "min_price": min_price,
        "max_price": max_price,
        "img_list": img_list,
        "expand_view_list": expand_view_list,
    }


def get_detail_info(driver, url: str):
    """打开商品详情页并解析，失败重试，返回解析结果或 None。"""
    result = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(url.strip())
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.CLASS_NAME, "expand-view-list"))
            )
        except Exception as exc:
            logger.warning("第 %d 次打开详情页超时：%s", attempt, exc)

        time.sleep(random.uniform(3.0, 6.0))
        result = parse_detail_html(
            BeautifulSoup(driver.page_source, "html.parser")
        )
        if result:
            break
    return result


def download_image_with_selenium(driver, img_url: str):
    """在浏览器中打开图片并用 Canvas 转成 JPEG 二进制，失败返回 None。

    部分图片直接请求会被风控拦截，因此复用浏览器会话截图下载。
    """
    img_data = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(img_url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "img"))
            )
            js_script = """
                var img = document.querySelector('img');
                if (!img) return null;
                var canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                canvas.getContext('2d').drawImage(img, 0, 0);
                return canvas.toDataURL('image/jpeg');
            """
            data_url = driver.execute_script(js_script)
            if not data_url:
                raise RuntimeError("无法获取图片 Base64 数据")
            img_data = base64.b64decode(data_url.split(",", 1)[1])
            if len(img_data) > MIN_IMAGE_SIZE:
                break
        except Exception as exc:
            logger.warning("图片下载失败（第 %d 次）：%s", attempt, exc)
    return img_data


def _should_skip(product: dict) -> bool:
    """按类目与上架时间判断是否跳过该商品。"""
    if product.get("类目") in SKIP_CATEGORIES:
        logger.info("跳过指定类目商品：%s", product.get("宝贝ID"))
        return True

    if MAX_PRODUCT_AGE_DAYS is not None:
        up_time = product.get("上架时间")
        if isinstance(up_time, str):
            up_time = dt.datetime.strptime(up_time, "%Y-%m-%d %H:%M:%S")
        if up_time and up_time < dt.datetime.now() - dt.timedelta(days=MAX_PRODUCT_AGE_DAYS):
            logger.info("跳过超过 %d 天的商品：%s", MAX_PRODUCT_AGE_DAYS, product.get("宝贝ID"))
            return True
    return False


def deal_company(driver, company_name: str) -> None:
    """采集一个店铺的全部商品详情与图片。"""
    init_tables()
    products = get_products_from_db(company_name)
    if not products:
        logger.warning("没有找到商品数据")
        return

    for index, product in enumerate(products, start=1):
        item_id = str(product.get("宝贝ID"))
        if _should_skip(product):
            continue

        logger.info(
            "%d/%d id=%s up_time=%s",
            index, len(products), item_id, product.get("上架时间"),
        )
        detail = get_detail_info(driver, product.get("宝贝链接"))
        if detail is None:
            continue

        product.update(detail)
        upsert_product(product)

        logger.info("下载图片 %d 张", len(detail["img_list"]))
        for img_index, img_url in enumerate(detail["img_list"], start=1):
            img_name = f"{img_index:02d}.jpg"
            # _.webp 为缩略图，_b.jpg 为大图
            img_data = download_image_with_selenium(
                driver, img_url.replace("_.webp", "_b.jpg")
            )
            if img_data is None:
                logger.warning("图片 %s 下载失败，跳过", img_name)
            else:
                upsert_image(company_name, item_id, img_name, img_data)
            time.sleep(1)

        logger.info("商品 %s 处理完成：%s", item_id,
                    dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        time.sleep(1)


def repair_first_image(driver, company_name: str,
                       excel_path: str, result_dir: str) -> None:
    """维护工具：根据历史 result.json 重新下载每个商品的首图（01.jpg）并入库。

    Args:
        driver: 已连接的 Chrome WebDriver 实例。
        company_name: 店铺/公司名称。
        excel_path: 商品 Excel 文件（读取其中的宝贝ID与上架时间）。
        result_dir: 按宝贝ID 分子目录存放 result.json 的目录。
    """
    import pandas as pd

    df = pd.read_excel(excel_path)
    df["宝贝ID"] = df["宝贝ID"].astype(str)
    for index, row in df.iterrows():
        item_id = row["宝贝ID"]
        logger.info("%d/%d id=%s up_time=%s",
                    index + 1, len(df), item_id, row["上架时间"])

        result_path = os.path.join(result_dir, item_id, "result.json")
        if not os.path.exists(result_path):
            continue
        with open(result_path, "r", encoding="utf-8") as f:
            img_list = json.load(f).get("img_list", [])
        if not img_list:
            continue

        img_data = download_image_with_selenium(
            driver, img_list[0].replace("_.webp", "_b.jpg")
        )
        if img_data:
            upsert_image(company_name, item_id, "01.jpg", img_data)
            logger.info("商品 %s 的 01.jpg 已修复", item_id)
        time.sleep(1)
    logger.info("修复完成")


def main() -> None:
    parser = argparse.ArgumentParser(description="采集 1688 商品详情与图片入库")
    parser.add_argument("company_name", help="公司/店铺名称（须与 product 表一致）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    driver = connect_chrome()
    try:
        deal_company(driver, args.company_name)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    logger.info("全部完成")


if __name__ == "__main__":
    main()
