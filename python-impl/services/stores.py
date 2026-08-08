# ============================================================
# 业务存储层 — MySQL 数据库实现（适配 smart_service 库真实表结构）
# ============================================================

from __future__ import annotations

import uuid
import logging
from datetime import datetime
from typing import Any

from infra.mysql import execute, fetchone, fetchall

logger = logging.getLogger(__name__)


# ── 订单存储 (order_summary 表) ──

class OrderStore:
    """订单存储，查询 order_summary 表。"""

    async def query(self, order_id: str, user_id: str = "") -> dict | None:
        if user_id:
            row = await fetchone(
                "SELECT o.order_id, o.user_id, o.username, o.user_phone, "
                "o.order_status, o.total_amount, o.shipping_address, "
                "o.carrier, o.tracking_no, o.logistics_status, "
                "o.payment_method, o.payment_status, o.payment_amount, "
                "o.products_json, o.risk_level, o.risk_score, o.created_at, o.updated_at, "
                "l.current_location, l.estimated_delivery "
                "FROM order_summary o "
                "LEFT JOIN logistics l ON o.order_id = l.order_id "
                "WHERE o.order_id = %s AND o.user_id = %s",
                (order_id, user_id),
            )
        else:
            row = await fetchone(
                "SELECT o.order_id, o.user_id, o.username, o.user_phone, "
                "o.order_status, o.total_amount, o.shipping_address, "
                "o.carrier, o.tracking_no, o.logistics_status, "
                "o.payment_method, o.payment_status, o.payment_amount, "
                "o.products_json, o.risk_level, o.risk_score, o.created_at, o.updated_at, "
                "l.current_location, l.estimated_delivery "
                "FROM order_summary o "
                "LEFT JOIN logistics l ON o.order_id = l.order_id "
                "WHERE o.order_id = %s",
                (order_id,),
            )
        if row is None:
            return None
        return {
            "found": True,
            "order": {
                "order_id": row["order_id"],
                "user_id": row["user_id"],
                "product": _parse_product(row.get("products_json")),
                "amount": float(row.get("total_amount") or 0),
                "status": row.get("order_status", ""),
                "logistics": f"{row.get('carrier','')} {row.get('tracking_no','')}".strip() or "暂无",
                "logistics_status": row.get("logistics_status", ""),
                "current_location": row.get("current_location") or "",
                "estimated_delivery": str(row.get("estimated_delivery") or ""),
                "payment": f"{row.get('payment_method','')} {row.get('payment_status','')}",
                "risk_level": row.get("risk_level", ""),
                "created_at": str(row.get("created_at", "")),
            },
        }

    async def query_by_user(self, user_id: str) -> list[dict]:
        rows = await fetchall(
            "SELECT o.order_id, o.user_id, o.order_status, o.total_amount, "
            "o.carrier, o.tracking_no, o.logistics_status, o.created_at, "
            "o.products_json, "
            "l.current_location, l.estimated_delivery "
            "FROM order_summary o "
            "LEFT JOIN logistics l ON o.order_id = l.order_id "
            "WHERE o.user_id = %s "
            "AND o.order_status IN ('pending', 'paid', 'shipped') "
            "ORDER BY o.created_at DESC",
            (user_id,),
        )
        return [
            {
                "order_id": r["order_id"],
                "user_id": r["user_id"],
                "status": r.get("order_status", ""),
                "amount": float(r.get("total_amount") or 0),
                "product": _parse_product(r.get("products_json")),
                "logistics": f"{r.get('carrier','')} {r.get('tracking_no','')}".strip() or "暂无",
                "current_location": r.get("current_location") or "",
                "estimated_delivery": str(r.get("estimated_delivery") or ""),
                "created_at": str(r.get("created_at", "")),
            }
            for r in rows
        ]


def _parse_product(products_json: str | None) -> str:
    """从 products_json 提取商品名。"""
    if not products_json:
        return "未知"
    try:
        import json
        items = json.loads(products_json)
        if isinstance(items, list) and len(items) > 0:
            return str(items[0].get("name", items[0])) if isinstance(items[0], dict) else str(items[0])
    except (json.JSONDecodeError, TypeError, KeyError, IndexError) as exc:
        logger.debug("_parse_product: failed to parse products_json, returning raw text[:100]. "
                    "error=%s, products_json[:200]=%s", exc, str(products_json)[:200])
    return products_json[:100]


# ── 工单存储 (tickets 表) ──

class TicketStore:
    """工单存储，操作 tickets 表。

    表字段映射（原代码 → 真实表）：
        ticket_type → type
        description → reason
    """

    @staticmethod
    def _gen_id() -> str:
        ts = datetime.now().strftime("%Y%m%d")
        return f"TKT-{ts}-{uuid.uuid4().hex[:6].upper()}"

    async def create(self, user_id: str, ticket_type: str, title: str,
                     description: str, priority: str = "medium",
                     order_id: str = "") -> dict:
        """创建工单。"""
        ticket_id = self._gen_id()
        now = datetime.now()
        await execute(
            "INSERT INTO tickets (ticket_id, order_id, user_id, title, type, "
            "priority, reason, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ticket_id, order_id or "", user_id, title, ticket_type,
             priority, description, "open", now, now),
        )
        return await self.query(ticket_id) or {}

    async def query(self, ticket_id: str, user_id: str = "") -> dict | None:
        if user_id:
            row = await fetchone(
                "SELECT ticket_id, order_id, user_id, title, type, priority, "
                "reason, status, assignee, resolution, created_at, updated_at "
                "FROM tickets WHERE ticket_id = %s AND user_id = %s",
                (ticket_id, user_id),
            )
        else:
            row = await fetchone(
                "SELECT ticket_id, order_id, user_id, title, type, priority, "
                "reason, status, assignee, resolution, created_at, updated_at "
                "FROM tickets WHERE ticket_id = %s",
                (ticket_id,),
            )
        if row is None:
            return None
        return {
            "found": True,
            "ticket": {
                "ticket_id": row["ticket_id"],
                "order_id": row.get("order_id", ""),
                "user_id": row["user_id"],
                "ticket_type": row.get("type", ""),
                "title": row.get("title", ""),
                "description": row.get("reason", ""),
                "status": row.get("status", ""),
                "priority": row.get("priority", ""),
                "assignee": row.get("assignee", ""),
                "resolution": row.get("resolution", ""),
                "created_at": str(row.get("created_at", "")),
                "updated_at": str(row.get("updated_at", "")),
            },
        }

    async def query_by_user(self, user_id: str) -> list[dict]:
        rows = await fetchall(
            "SELECT ticket_id, title, type, priority, status, created_at "
            "FROM tickets WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [
            {
                "ticket_id": r["ticket_id"],
                "title": r.get("title", ""),
                "ticket_type": r.get("type", ""),
                "priority": r.get("priority", ""),
                "status": r.get("status", ""),
                "created_at": str(r.get("created_at", "")),
            }
            for r in rows
        ]

    async def query_by_user_and_type(self, user_id: str, ticket_type: str) -> list[dict]:
        """按用户 + 工单类型查询已有工单（用于去重检查）。"""
        rows = await fetchall(
            "SELECT ticket_id, order_id, user_id, title, type, priority, "
            "reason, status, assignee, resolution, created_at, updated_at "
            "FROM tickets WHERE user_id = %s AND type = %s AND status != 'closed' "
            "ORDER BY created_at DESC",
            (user_id, ticket_type),
        )
        return [
            {
                "ticket_id": r["ticket_id"],
                "order_id": r.get("order_id", ""),
                "user_id": r["user_id"],
                "ticket_type": r.get("type", ""),
                "title": r.get("title", ""),
                "description": r.get("reason", ""),
                "status": r.get("status", ""),
                "priority": r.get("priority", ""),
                "assignee": r.get("assignee", ""),
                "resolution": r.get("resolution", ""),
                "created_at": str(r.get("created_at", "")),
                "updated_at": str(r.get("updated_at", "")),
            }
            for r in rows
        ]

    async def query_by_order(self, order_id: str) -> list[dict]:
        """按关联订单号查询工单列表。"""
        rows = await fetchall(
            "SELECT ticket_id, order_id, user_id, title, type, priority, "
            "reason, status, assignee, resolution, created_at, updated_at "
            "FROM tickets WHERE order_id = %s ORDER BY created_at DESC",
            (order_id,),
        )
        return [
            {
                "ticket_id": r["ticket_id"],
                "order_id": r.get("order_id", ""),
                "user_id": r["user_id"],
                "ticket_type": r.get("type", ""),
                "title": r.get("title", ""),
                "description": r.get("reason", ""),
                "status": r.get("status", ""),
                "priority": r.get("priority", ""),
                "assignee": r.get("assignee", ""),
                "resolution": r.get("resolution", ""),
                "created_at": str(r.get("created_at", "")),
                "updated_at": str(r.get("updated_at", "")),
            }
            for r in rows
        ]

    async def update(self, ticket_id: str, status: str | None = None,
                     priority: str | None = None,
                     description: str | None = None) -> dict | None:
        """更新工单。"""
        sets: list[str] = []
        args: list[Any] = []

        if status is not None:
            sets.append("status = %s")
            args.append(status)
        if priority is not None:
            sets.append("priority = %s")
            args.append(priority)
        if description is not None:
            sets.append("reason = %s")
            args.append(description)

        if not sets:
            return await self.query(ticket_id)

        sets.append("updated_at = %s")
        args.append(datetime.now())
        args.append(ticket_id)

        sql = f"UPDATE tickets SET {', '.join(sets)} WHERE ticket_id = %s"
        affected = await execute(sql, tuple(args))

        if affected == 0:
            return None
        return await self.query(ticket_id)


# ── 物流存储 (logistics 表) ──

class LogisticsStore:
    """物流存储，按运单号查询物流详情（含关联订单信息）。"""

    async def query_by_tracking(self, tracking_no: str) -> dict | None:
        row = await fetchone(
            "SELECT l.tracking_no, l.carrier, l.status, l.origin, l.destination, "
            "l.current_location, l.estimated_delivery, l.shipped_at, l.delivered_at, "
            "o.order_id, o.user_id, o.order_status, o.total_amount, o.products_json "
            "FROM logistics l "
            "LEFT JOIN order_summary o ON l.order_id = o.order_id "
            "WHERE l.tracking_no = %s",
            (tracking_no,),
        )
        if row is None:
            return None
        return {
            "found": True,
            "tracking_no": row["tracking_no"],
            "carrier": row["carrier"],
            "status": row["status"],
            "origin": row.get("origin", ""),
            "destination": row.get("destination", ""),
            "current_location": row.get("current_location") or "",
            "estimated_delivery": str(row.get("estimated_delivery") or ""),
            "shipped_at": str(row.get("shipped_at") or ""),
            "delivered_at": str(row.get("delivered_at") or ""),
            "order": {
                "order_id": row.get("order_id", ""),
                "user_id": row.get("user_id", ""),
                "order_status": row.get("order_status", ""),
                "amount": float(row.get("total_amount") or 0),
                "product": _parse_product(row.get("products_json")),
            },
        }


# ── 用户画像存储 (user_profiles 表) ──

class UserProfileStore:
    """用户画像存储，查询 user_profiles 表。"""

    async def query(self, user_id: str) -> dict | None:
        row = await fetchone(
            "SELECT user_id, name, level, balance, points, phone, email, register_date "
            "FROM user_profiles WHERE user_id = %s",
            (user_id,),
        )
        if row is None:
            return None
        return self._safe(row)

    async def risk_check(self, user_id: str, amount: float) -> dict:
        """风控评估：基于用户等级 + 交易金额。"""
        profile = await fetchone(
            "SELECT level, balance FROM user_profiles WHERE user_id = %s",
            (user_id,),
        )
        if not profile:
            return {"risk_level": "unknown", "reason": "用户不存在", "allow": False}

        risk_level = "low"
        reasons: list[str] = []

        if amount > 50000:
            risk_level = "high"
            reasons.append("交易金额超过 50000 元")
        elif amount > 10000:
            risk_level = "medium"
            reasons.append("交易金额超过 10000 元")

        balance = float(profile.get("balance") or 0)
        if balance < amount:
            risk_level = "high"
            reasons.append("账户余额不足")

        level = profile.get("level", "")
        if level == "silver" and amount > 5000:
            levels = ["low", "medium", "high", "critical"]
            risk_level = levels[max(levels.index(risk_level), levels.index("medium"))]
            reasons.append("银卡会员单笔限额 5000 元")

        return {
            "risk_level": risk_level,
            "reasons": reasons,
            "allow": risk_level != "critical",
            "user_level": level,
            "amount": amount,
        }

    @staticmethod
    def _safe(profile: dict) -> dict:
        """脱敏处理。"""
        name = str(profile.get("name", ""))
        phone = str(profile.get("phone", ""))
        email = str(profile.get("email", ""))

        return {
            "user_id": profile.get("user_id"),
            "name": name[:1] + "**" if name else "***",
            "level": profile.get("level"),
            "balance": float(profile.get("balance") or 0),
            "points": int(profile.get("points") or 0),
            "phone": phone[:3] + "****" + phone[-4:] if len(phone) > 7 else "***",
            "email": (email.split("@")[0][:2] + "***@" + email.split("@")[-1]
                      if "@" in email else "***"),
            "register_date": str(profile.get("register_date", "")),
        }
