# product_1688_to_pdd

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Chrome-important)](https://www.google.com/chrome/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 一套「1688 选品 → 图片采集 → 拼多多以图搜货比价 → 商品视频搬运上传」的
> 半自动化辅助工具集，适合个人无货源电商的选品调研与素材整理场景。

---

## ⚠️ 免责声明（请务必先阅读）

1. 本项目**仅供学习与技术研究使用**，不提供任何形式的明示或暗示担保。
2. 请严格遵守 1688、拼多多、微信等平台的用户协议、robots 协议及当地法律法规，
   自行评估账号风控风险，**请勿高频、大规模采集**。
3. 商品图片、视频等素材的版权归原权利人所有，请勿将本工具用于侵权、
   刷单、搬运假货等违规经营行为；由此产生的一切后果由使用者自行承担。
4. 项目通过**附着用户手动登录的浏览器**完成操作，不包含任何破解、
   绕过验证码或官方接口的能力。

---

## 一、项目简介

本工具集围绕以下选品铺货链路设计（🟦 自动化步骤 / 🟨 人工步骤）：

```mermaid
graph LR
    A["1️⃣ 关键词搜索<br/>1688 工厂店铺"] --> B["🟨 人工筛选优质店铺<br/>采购助手导出 Excel"]
    B --> C["2️⃣ Excel 导入<br/>MySQL product 表"]
    C --> D["3️⃣ 采集商品详情<br/>与商品图片入库"]
    D --> E["4️⃣ 微信拼多多小程序<br/>以图搜货 + 相似度比价"]
    E --> F["🟨 按差价人工选品<br/>第三方工具铺货上架"]
    F --> G["5️⃣ 下载 1688<br/>商品讲解视频"]
    G --> H["6️⃣ 拼多多商家后台<br/>批量上传讲解视频"]
```

| 模块 | 能力 | 运行环境 |
| --- | --- | --- |
| `search_1688_factory.py` | 按关键词分页搜索 1688 工厂/商家，导出店铺指标 CSV | Chrome + Selenium |
| `get_1688_img.py` | 解析商品详情（标题/阶梯价/SKU/主图）并下载图片入库 | Chrome + MySQL |
| `wx_pdd_img_search.py` | PC 微信拼多多小程序以图搜货，pHash/SSIM/ORB 同款判定与比价 | Windows + PC 微信 |
| `get_1688_video.py` | 在 1688 采购助手工作台批量下载商品讲解视频 | Chrome + Selenium |
| `pdd_video_uploader.py` | 在拼多多商家后台按商品 ID 批量上传讲解视频 | Windows + Chrome |

---

## 二、目录结构

```text
product_1688_to_pdd/
├── README.md
├── LICENSE                      # MIT 许可证
├── .env.example                 # 环境变量配置样例（复制为 .env 使用）
├── .gitignore
├── requirements.txt
├── data/                        # 运行期数据（CSV/图片/视频/日志），仓库仅保留样例
│   ├── product_ids.example.txt  #   视频下载用的商品 ID 列清单样例
│   └── product_mapping.example.csv  # 视频上传映射表样例
├── sql/
│   └── schema.sql               # 全部数据库表结构
└── src/
    ├── config.py                # 统一配置（读取环境变量 / .env）
    ├── db.py                    # MySQL 连接
    ├── browser.py               # Chrome 远程调试连接 + stealth 注入
    ├── search_1688_factory.py   # ① 工厂店铺搜索
    ├── get_1688_img.py          # ③ 商品详情与图片采集
    ├── wx_pdd_img_search.py     # ④ 拼多多以图搜货比价
    ├── get_1688_video.py        # ⑤ 商品视频下载
    └── pdd_video_uploader.py    # ⑥ 拼多多视频批量上传
```

---

## 三、环境要求

- **操作系统**：Windows 10/11（微信小程序 UI 自动化、系统文件对话框自动化仅支持 Windows；
  工厂搜索与图片采集理论上可在 macOS/Linux 运行，但未做适配验证）
- **Python**：3.9 及以上
- **Chrome 浏览器**（与本机 Chrome 版本匹配的 ChromeDriver 会由 Selenium 4 自动管理）
- **MySQL**：5.7 或 8.0
- **PC 版微信**：仅 `wx_pdd_img_search.py` 需要，并需能在微信内打开拼多多小程序

---

## 四、安装与初始化

### 1. 获取代码并安装依赖

```bash
git clone https://github.com/<你的用户名>/product_1688_to_pdd.git
cd product_1688_to_pdd

python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# 或 CMD
.venv\Scripts\activate.bat

pip install -r requirements.txt
```

### 2. 配置数据库等环境变量

```bash
copy .env.example .env
```

编辑 `.env`，填入你自己的 MySQL 账号密码等信息（`.env` 已被 git 忽略，不会提交）：

```ini
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的数据库密码
MYSQL_DATABASE=ebusiness
CHROME_DEBUG_ADDRESS=127.0.0.1:9222
```

### 3. 初始化数据库表

```bash
mysql -u root -p < sql/schema.sql
```

脚本会自动创建 `product`、`product_1688`、`img_1688`、
`product_pdd_compare`、`pdd_search_item` 五张表，字段说明见 [sql/schema.sql](sql/schema.sql)。

### 4. 以远程调试模式启动 Chrome

所有 Chrome 相关脚本都**附着到一个你手动启动并登录好的 Chrome**，以复用登录态：

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="C:\chrome-debug-profile"
```

> macOS/Linux 将路径替换为对应 Chrome 可执行文件路径即可。

### 5. （可选）安装 stealth.min.js

部分站点会识别 Selenium 自动化特征。可自行下载 `stealth.min.js`
（源于 puppeteer-extra-stealth 生态的反检测脚本）放到项目根目录，
`browser.py` 启动时会自动注入；**不放置也可以运行**，仅会输出一条告警。

---

## 五、使用流程

> 以下命令均在**项目根目录**执行；脚本内的相对路径（如 `data/`）
> 由 [config.py](src/config.py) 基于项目根目录解析，与当前工作目录无关。

### ① 搜索 1688 工厂店铺

```bash
python src/search_1688_factory.py -k 饰品 -p 20
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `-k, --keyword` | 搜索关键词（按 GBK 编码） | `饰品` |
| `-p, --pages` | 抓取页数 | `20` |
| `-o, --output` | CSV 输出路径 | `data/factory/1688_factory_<关键词>_<时间>.csv` |

### 🟨 人工筛选 + Excel 入库（一次性准备）

1. 打开搜索结果 CSV，人工挑出优质工厂店；
2. 通过 **1688 官方采购助手 Chrome 插件**进入店铺，浏览全部商品并勾选
   **支持一件代发**的商品，使用插件的导出功能得到 Excel；
3. 将 Excel 导入 MySQL 的 `product` 表（字段：宝贝ID、公司名称、宝贝链接、上架时间、类目）。
   可使用 Navicat 导入向导、`LOAD DATA LOCAL INFILE` 或 pandas：

   ```python
   import pandas as pd
   from sqlalchemy import create_engine
   df = pd.read_excel("商品导出.xlsx")
   engine = create_engine("mysql+pymysql://root:密码@127.0.0.1:3306/ebusiness?charset=utf8mb4")
   df.to_sql("product", engine, if_exists="append", index=False)
   ```

### ③ 采集商品详情与图片

在调试模式的 Chrome 中登录 1688 后执行：

```bash
python src/get_1688_img.py "某某饰品有限公司"
```

- 结构化详情写入 `product_1688`，图片二进制写入 `img_1688`；
- 需要跳过某些类目或只采集近期商品时，修改 [get_1688_img.py](src/get_1688_img.py)
  顶部的 `SKIP_CATEGORIES`、`MAX_PRODUCT_AGE_DAYS` 常量。

### ④ 微信拼多多以图搜货比价

1. 在 PC 微信中打开**拼多多小程序**并停留在首页（建议小程序窗口尺寸保持默认）；
2. 执行：

```bash
python src/wx_pdd_img_search.py "某某饰品有限公司"
```

可选参数：`--pic-dir`（搜索用图临时目录）、`--result-dir`（结果截图目录），
默认分别为 `data/search_pic/<公司名>/` 与 `data/pdd_result/<公司名>/`。

比价汇总写入 `product_pdd_compare`（同款数、同款最低/最高价、搜索最低价、均价），
每条搜索结果写入 `pdd_search_item`。

> **同款判定**：pHash > 0.90、SSIM > 0.85、ORB 匹配率 > 0.15，任一满足即视为同款，
> 阈值可在脚本顶部 `*_THRESHOLD` 常量中调整。

### 🟨 人工选品与铺货

依据 `product_pdd_compare` 表中的差价（1688 价 vs 拼多多同款价）人工选品，
通过第三方铺货软件上架到自己的拼多多店铺，并记录「拼多多商品ID ↔ 来源商品ID」的对应关系。

### ⑤ 下载 1688 商品讲解视频

在调试模式 Chrome 中登录 1688 采购助手，手动打开
**「优选工作台 - 应用容器」的商品管理列表页**，然后执行：

```bash
# 全量模式：从第一页起逐页下载（默认 6 页）
python src/get_1688_video.py --max-pages 6

# 指定 ID 模式：只下载清单中的商品
copy data\product_ids.example.txt data\product_ids.txt
# 编辑 data/product_ids.txt，每行一个商品ID
python src/get_1688_video.py --ids data/product_ids.txt
```

视频默认保存到 `data/videos/<商品ID>.mp4`，已存在的文件会自动跳过，
可用 `-o/--out-dir` 自定义输出目录。

### ⑥ 拼多多商家后台批量上传视频

1. 准备映射表：复制 `data/product_mapping.example.csv` 为
   `data/product_mapping.csv`，每行格式为
   `拼多多商品ID, 商品链接, 视频ID`（视频文件名即 `<视频ID>.mp4`）；
2. 在调试模式 Chrome 中登录拼多多商家后台，打开
   **「多多视频 - 商品讲解」页面**；
3. 执行：

```bash
# 先小批量试跑 2 个
python src/pdd_video_uploader.py --limit 2 --verbose

# 正式批量上传
python src/pdd_video_uploader.py -m data/product_mapping.csv -d data/videos
```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `-m, --mapping` | 商品视频映射表 | `data/product_mapping.csv` |
| `-d, --video-dir` | 视频文件目录 | `data/videos` |
| `--limit` | 仅上传前 N 个商品 | 不限制 |
| `--debugger-address` | Chrome 调试地址 | `.env` 中的配置 |
| `--verbose` | 输出调试级日志 | 关闭 |

运行日志同时写入 `data/logs/pdd_video_upload.log`。

---

## 六、数据表说明

| 表名 | 写入模块 | 内容 |
| --- | --- | --- |
| `product` | 人工导入 | 采购助手导出的原始商品清单 |
| `product_1688` | `get_1688_img.py` | 详情页解析结果（标题、价格区间、SKU、图片URL） |
| `img_1688` | `get_1688_img.py` | 商品图片二进制 |
| `product_pdd_compare` | `wx_pdd_img_search.py` | 每个商品的比价汇总指标 |
| `pdd_search_item` | `wx_pdd_img_search.py` | 拼多多搜索结果明细（标题、价格、是否同款、缩略图） |

---

## 七、已知限制与常见问题

1. **页面/小程序改版导致选择器失效**
   1688、拼多多后台及微信小程序的 DOM/控件树会不定期变化。脚本已对关键元素采用
   「多选择器 + JS 兜底」策略，仍失效时可用浏览器开发者工具或
   [Accessibility Insights / inspect.exe] 重新标定，并修改脚本顶部的定位常量。

2. **微信小程序点击坐标不生效**
   `wx_pdd_img_search.py` 顶部的 `*_XY` 坐标基于约 540×960 的小程序窗口实测。
   若分辨率/缩放比例/微信版本不同，请用 `inspect.exe` 或 pyautogui 的
   `position()` 重新标定坐标常量。

3. **连接 9222 端口失败**
   确认 Chrome 启动参数包含 `--remote-debugging-port=9222`，
   且该 Chrome 实例是在启动参数下新打开的（普通 Chrome 不会开放调试端口）。

4. **被风控/出现验证码**
   脚本已内置随机等待与限速，请进一步调大间隔时间、降低单次处理量；
   验证码需要人工处理后再继续。

5. **视频直接下载返回 403**
   属正常现象，脚本已改为携带浏览器 Cookie 与 Referer 的 requests 会话下载。

6. **图片相似度误判**
   宣传图常含边框/文案，可按自己的品类调整 `PHASH_THRESHOLD / SSIM_THRESHOLD /
   ORB_THRESHOLD`，或改成多图投票策略。

---

## 八、贡献指南

欢迎通过 Issue 反馈问题或提交 PR：

1. Fork 本仓库并新建特性分支（`feat/xxx`、`fix/xxx`）；
2. 提交前请确认**不含任何账号、密码、Cookie、真实店铺名称等隐私信息**；
3. 在 PR 中说明改动点、测试方式与适用的平台/软件版本。

---

## 九、许可证

本项目基于 [MIT License](LICENSE) 开源。使用即代表你已阅读并同意上述免责声明。
