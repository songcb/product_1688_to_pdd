# -*- coding: utf-8 -*-
"""MySQL 数据库连接公共模块。"""
from __future__ import annotations

import logging

import pymysql

from config import MYSQL_CONFIG

logger = logging.getLogger(__name__)


def get_connection():
    """创建一个 MySQL 连接，调用方负责 close()。"""
    return pymysql.connect(**MYSQL_CONFIG)
