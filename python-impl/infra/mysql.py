# ============================================================
# MySQL 连接管理 — aiomysql 异步连接池（支持懒加载）
# ============================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiomysql

logger = logging.getLogger(__name__)

# 全局连接池单例
_pool: aiomysql.Pool | None = None
_init_lock = asyncio.Lock()


async def init_mysql_pool(settings) -> aiomysql.Pool | None:
    """初始化 MySQL 异步连接池。连接失败时优雅降级，不阻塞启动。"""
    global _pool
    try:
        pool = await aiomysql.create_pool(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            db=settings.mysql_database,
            minsize=1,
            maxsize=settings.mysql_pool_size,
            autocommit=True,
            charset="utf8mb4",
        )
        _pool = pool
        logger.info("MySQL pool initialized: %s@%s:%d/%s (size=%d)",
                     settings.mysql_user, settings.mysql_host,
                     settings.mysql_port, settings.mysql_database,
                     settings.mysql_pool_size)
        return pool
    except Exception as exc:
        logger.warning("MySQL unavailable (%s), service will run without database", exc)
        return None


async def _lazy_init() -> aiomysql.Pool:
    """懒加载：首次调用时自动初始化连接池（子进程脚本使用）。"""
    global _pool
    if _pool is not None:
        return _pool

    async with _init_lock:
        if _pool is not None:
            return _pool
        # 子进程中没有 settings 单例，从 infra.config 延迟导入
        from infra.config import settings
        pool = await init_mysql_pool(settings)
        if pool is None:
            raise RuntimeError("MySQL pool failed to initialize")
        return pool


async def close_mysql_pool() -> None:
    """关闭 MySQL 连接池。"""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MySQL pool closed")


async def get_pool() -> aiomysql.Pool | None:
    """获取全局连接池。"""
    return _pool


async def _ensure_pool() -> aiomysql.Pool:
    """确保连接池可用，未初始化则懒加载。"""
    if _pool is not None:
        return _pool
    return await _lazy_init()


async def execute(sql: str, args: tuple | None = None) -> int:
    """执行写操作（INSERT/UPDATE/DELETE），返回影响行数。"""
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, args)
            return cursor.rowcount


async def fetchone(sql: str, args: tuple | None = None) -> dict[str, Any] | None:
    """执行查询，返回单行 dict。"""
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(sql, args)
            return await cursor.fetchone()


async def fetchall(sql: str, args: tuple | None = None) -> list[dict[str, Any]]:
    """执行查询，返回多行 dict 列表。"""
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(sql, args)
            return await cursor.fetchall()
