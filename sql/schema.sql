-- =============================================================================
-- product_1688_to_pdd 数据库结构
-- 用法：mysql -u root -p < sql/schema.sql
-- =============================================================================

CREATE DATABASE IF NOT EXISTS `ebusiness`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE `ebusiness`;

-- -----------------------------------------------------------------------------
-- 1. product：1688 官方采购助手导出的原始商品表
--    数据来源：采购助手 Chrome 插件导出的 Excel（仅导出支持一件代发的商品），
--    可通过 Navicat 导入向导 / LOAD DATA / pandas.to_sql 等方式入库。
--    下列字段为脚本实际使用的字段，Excel 中的其他列可按需追加。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `product` (
    `宝贝ID`   VARCHAR(50)  NOT NULL COMMENT '1688 商品ID',
    `公司名称` VARCHAR(255) NOT NULL COMMENT '供应商/工厂店铺名称',
    `宝贝链接` VARCHAR(500) NOT NULL COMMENT '1688 商品详情页 URL',
    `上架时间` DATETIME     NULL     COMMENT '商品上架时间',
    `类目`     VARCHAR(255) NULL     COMMENT '商品类目',
    PRIMARY KEY (`宝贝ID`),
    KEY `idx_company` (`公司名称`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购助手导出的原始商品表';

-- -----------------------------------------------------------------------------
-- 2. product_1688：脚本解析详情页后得到的结构化商品信息（get_1688_img.py 写入）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `product_1688` (
    `宝贝ID`          VARCHAR(50)  NOT NULL COMMENT '1688 商品ID',
    `公司名称`        VARCHAR(255) NOT NULL COMMENT '供应商/工厂店铺名称',
    `宝贝链接`        VARCHAR(500) NOT NULL COMMENT '1688 商品详情页 URL',
    `上架时间`        DATETIME     NULL     COMMENT '商品上架时间',
    `类目`            VARCHAR(255) NULL     COMMENT '商品类目',
    `title`           VARCHAR(500) NULL     COMMENT '详情页商品标题',
    `min_price`       DECIMAL(10,2) NULL   COMMENT '1688 最低价（元）',
    `max_price`       DECIMAL(10,2) NULL   COMMENT '1688 最高价（元）',
    `img_list`        TEXT         NULL     COMMENT '主图 URL 列表（JSON 数组）',
    `expand_view_list` TEXT        NULL     COMMENT 'SKU 属性与价格（JSON 数组）',
    `update_time`     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`宝贝ID`),
    KEY `idx_company` (`公司名称`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='1688 商品详情解析结果';

-- -----------------------------------------------------------------------------
-- 3. img_1688：1688 商品图片二进制（get_1688_img.py 写入）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `img_1688` (
    `公司名称` VARCHAR(255) NOT NULL COMMENT '供应商/工厂店铺名称',
    `宝贝ID`   VARCHAR(50)  NOT NULL COMMENT '1688 商品ID',
    `图片名称` VARCHAR(50)  NOT NULL COMMENT '图片序号名，如 01.jpg',
    `img_data` LONGBLOB     NOT NULL COMMENT '图片二进制内容',
    `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`公司名称`, `宝贝ID`, `图片名称`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='1688 商品图片';

-- -----------------------------------------------------------------------------
-- 4. product_pdd_compare：每个 1688 商品在拼多多的比价汇总
--    （wx_pdd_img_search.py 写入）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `product_pdd_compare` (
    `宝贝ID`          VARCHAR(50)  NOT NULL COMMENT '1688 商品ID',
    `公司名称`        VARCHAR(255) NOT NULL COMMENT '供应商/工厂店铺名称',
    `宝贝链接`        VARCHAR(500) NOT NULL COMMENT '1688 商品详情页 URL',
    `上架时间`        DATETIME     NULL     COMMENT '商品上架时间',
    `类目`            VARCHAR(255) NULL     COMMENT '商品类目',
    `title`           VARCHAR(500) NULL     COMMENT '商品标题',
    `min_price`       DECIMAL(10,2) NULL   COMMENT '1688 最低价（元）',
    `max_price`       DECIMAL(10,2) NULL   COMMENT '1688 最高价（元）',
    `sim_count`       INT          DEFAULT 0 COMMENT '判定为同款的搜索结果数量',
    `sim_min_price`   DECIMAL(10,2) NULL   COMMENT '同款商品最低价（元）',
    `sim_max_price`   DECIMAL(10,2) NULL   COMMENT '同款商品最高价（元）',
    `search_min_price` DECIMAL(10,2) NULL  COMMENT '全部搜索结果最低价（元）',
    `search_avg_price` DECIMAL(10,2) NULL  COMMENT '全部搜索结果平均价（元）',
    `update_time`     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`宝贝ID`),
    KEY `idx_company` (`公司名称`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='1688 vs 拼多多 比价汇总';

-- -----------------------------------------------------------------------------
-- 5. pdd_search_item：拼多多以图搜货的每条结果明细
--    （wx_pdd_img_search.py 写入）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `pdd_search_item` (
    `公司名称`  VARCHAR(255) NOT NULL COMMENT '供应商/工厂店铺名称',
    `宝贝ID`    VARCHAR(50)  NOT NULL COMMENT '1688 商品ID',
    `search_idx` INT         NULL     COMMENT '搜索结果序号（从 1 开始）',
    `title`     VARCHAR(255) NOT NULL COMMENT '拼多多搜索结果标题',
    `price_list` VARCHAR(255) NOT NULL COMMENT '价格区原始文本',
    `min_price` DECIMAL(10,2) NULL   COMMENT '解析出的最低价（元）',
    `is_ssim`   BOOLEAN       NULL     COMMENT '是否与 1688 主图判定为同款',
    `img_data`  LONGBLOB      NOT NULL COMMENT '搜索结果缩略图二进制',
    `update_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`公司名称`, `宝贝ID`, `search_idx`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='拼多多以图搜货结果明细';
