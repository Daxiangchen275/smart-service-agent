#!/usr/bin/env python3
"""删除并重建 smart_service 库全部 10 张表。"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.mysql import init_mysql_pool, close_mysql_pool, execute, fetchall
from infra.config import settings


async def rebuild():
    pool = await init_mysql_pool(settings)
    if pool is None:
        print("MySQL not available")
        return

    # ── 删除所有表 ──
    tables = ["tickets", "order_summary", "user_profiles",
              "risk_records", "payments", "logistics",
              "order_items", "orders", "products", "users"]
    for t in tables:
        await execute(f"DROP TABLE IF EXISTS {t}")
        print(f"Dropped: {t}")

    # ── users ──
    await execute("""CREATE TABLE users (
        id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(50) NOT NULL UNIQUE,
        username VARCHAR(100), phone VARCHAR(20), created_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── products ──
    await execute("""CREATE TABLE products (
        id INT AUTO_INCREMENT PRIMARY KEY, product_id VARCHAR(50) NOT NULL UNIQUE,
        name VARCHAR(200) NOT NULL, category VARCHAR(50) NOT NULL,
        price FLOAT NOT NULL, stock INT NOT NULL,
        description TEXT, image_url VARCHAR(500), created_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── orders ──
    await execute("""CREATE TABLE orders (
        id INT AUTO_INCREMENT PRIMARY KEY, order_id VARCHAR(50) NOT NULL UNIQUE,
        user_id VARCHAR(50) NOT NULL, status VARCHAR(20) NOT NULL,
        total_amount FLOAT NOT NULL, shipping_address TEXT,
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        INDEX idx_orders_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── order_items ──
    await execute("""CREATE TABLE order_items (
        id INT AUTO_INCREMENT PRIMARY KEY, order_id VARCHAR(50) NOT NULL,
        product_id VARCHAR(50) NOT NULL, quantity INT NOT NULL,
        unit_price FLOAT NOT NULL,
        INDEX idx_oi_order_id (order_id), INDEX idx_oi_product_id (product_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── logistics ──
    await execute("""CREATE TABLE logistics (
        id INT AUTO_INCREMENT PRIMARY KEY, order_id VARCHAR(50) NOT NULL UNIQUE,
        tracking_no VARCHAR(50) NOT NULL, carrier VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL, origin VARCHAR(200),
        destination VARCHAR(200), shipped_at DATETIME,
        delivered_at DATETIME, updated_at DATETIME
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── payments ──
    await execute("""CREATE TABLE payments (
        id INT AUTO_INCREMENT PRIMARY KEY, transaction_id VARCHAR(50) NOT NULL UNIQUE,
        order_id VARCHAR(50) NOT NULL, user_id VARCHAR(50) NOT NULL,
        amount FLOAT NOT NULL, method VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL,
        INDEX idx_pay_order_id (order_id), INDEX idx_pay_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── risk_records ──
    await execute("""CREATE TABLE risk_records (
        id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(50) NOT NULL,
        rule_id VARCHAR(10) NOT NULL, rule_desc VARCHAR(100) NOT NULL,
        score INT NOT NULL, context VARCHAR(30) NOT NULL,
        created_at DATETIME NOT NULL, INDEX idx_risk_user_id (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

    # ── user_profiles ──
    await execute("""CREATE TABLE user_profiles (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(32) NOT NULL UNIQUE,
        name VARCHAR(64) NOT NULL DEFAULT '', level VARCHAR(20) NOT NULL DEFAULT 'silver',
        balance DECIMAL(14,2) NOT NULL DEFAULT 0.00, phone VARCHAR(20) NOT NULL DEFAULT '',
        email VARCHAR(128) NOT NULL DEFAULT '', register_date VARCHAR(32) NOT NULL DEFAULT '',
        INDEX idx_profile_level (level)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

    # ── order_summary ──
    await execute("""CREATE TABLE order_summary (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id VARCHAR(32) NOT NULL UNIQUE,
        user_id VARCHAR(32) NOT NULL, username VARCHAR(64) NOT NULL DEFAULT '',
        user_phone VARCHAR(20) NOT NULL DEFAULT '', order_status VARCHAR(20) NOT NULL DEFAULT 'pending',
        total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
        shipping_address VARCHAR(512) NOT NULL DEFAULT '',
        carrier VARCHAR(64) NOT NULL DEFAULT '', tracking_no VARCHAR(64) NOT NULL DEFAULT '',
        logistics_status VARCHAR(64) NOT NULL DEFAULT '',
        payment_method VARCHAR(32) NOT NULL DEFAULT '',
        payment_status VARCHAR(20) NOT NULL DEFAULT '',
        payment_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
        products_json TEXT, risk_level VARCHAR(10) NOT NULL DEFAULT '',
        risk_score DECIMAL(5,2) NOT NULL DEFAULT 0.00,
        created_at VARCHAR(32) NOT NULL DEFAULT '', updated_at VARCHAR(32) NOT NULL DEFAULT '',
        INDEX idx_os_user_id (user_id), INDEX idx_os_status (order_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

    # ── tickets ──
    await execute("""CREATE TABLE tickets (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, ticket_id VARCHAR(32) NOT NULL UNIQUE,
        order_id VARCHAR(32) NOT NULL DEFAULT '', user_id VARCHAR(32) NOT NULL,
        title VARCHAR(256) NOT NULL DEFAULT '', type VARCHAR(20) NOT NULL DEFAULT 'general',
        priority VARCHAR(10) NOT NULL DEFAULT 'medium', reason TEXT,
        status VARCHAR(20) NOT NULL DEFAULT 'open', assignee VARCHAR(64) NOT NULL DEFAULT '',
        resolution TEXT, created_at VARCHAR(32) NOT NULL DEFAULT '',
        updated_at VARCHAR(32) NOT NULL DEFAULT '',
        INDEX idx_tk_user_id (user_id), INDEX idx_tk_status (status), INDEX idx_tk_type (type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

    # ── Seed Data ──
    await execute("""INSERT INTO users (user_id, username, phone, created_at) VALUES
        ('user-1001','张三','13800138001','2024-01-15 10:00:00'),
        ('user-1002','李四','13900139002','2025-06-01 10:00:00'),
        ('user-1003','王五','13700137003','2023-03-20 10:00:00')""")

    await execute("""INSERT INTO products (product_id, name, category, price, stock, description, created_at) VALUES
        ('P001','iPhone 15 Pro','手机',8999,50,'A17 Pro芯片钛金属边框','2026-01-01'),
        ('P002','AirPods Pro','配件',1899,200,'主动降噪自适应音频','2026-01-01'),
        ('P003','MacBook Air M3','笔记本',10499,30,'M3芯片13.6英寸','2026-01-01'),
        ('P004','华为Mate 80 Pro','手机',7999,40,'麒麟芯片卫星通信','2026-01-01'),
        ('P005','华为Watch GT','配件',2499,100,'14天续航心率监测','2026-01-01')""")

    await execute("""INSERT INTO orders (order_id, user_id, status, total_amount, shipping_address, created_at, updated_at) VALUES
        ('ORD-20260401-A001','user-1001','shipped',8999,'北京市朝阳区xxx路1号','2026-04-01','2026-04-01'),
        ('ORD-20260401-A002','user-1001','delivered',1899,'北京市朝阳区xxx路1号','2026-03-28','2026-03-30'),
        ('ORD-20260401-A003','user-1002','pending',10499,'上海市浦东新区xxx路2号','2026-04-02','2026-04-02'),
        ('ORD-20260401-A004','user-1003','shipped',52000,'广州市天河区xxx路3号','2026-04-03','2026-04-03')""")

    await execute("""INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
        ('ORD-20260401-A001','P001',1,8999),
        ('ORD-20260401-A002','P002',1,1899),
        ('ORD-20260401-A003','P003',1,10499),
        ('ORD-20260401-A004','P004',2,7999),
        ('ORD-20260401-A004','P005',1,2499)""")

    await execute("""INSERT INTO logistics (order_id, tracking_no, carrier, status, origin, destination, shipped_at) VALUES
        ('ORD-20260401-A001','SF1234567890','顺丰','in_transit','上海仓','北京市朝阳区','2026-04-01 12:00:00'),
        ('ORD-20260401-A002','YT9876543210','圆通','delivered','深圳仓','北京市朝阳区','2026-03-28 14:00:00'),
        ('ORD-20260401-A004','JD1122334455','京东','in_transit','广州仓','广州市天河区','2026-04-03 09:00:00')""")

    await execute("""INSERT INTO payments (transaction_id, order_id, user_id, amount, method, status, created_at) VALUES
        ('TXN-001','ORD-20260401-A001','user-1001',8999,'微信支付','success','2026-04-01 10:05:00'),
        ('TXN-002','ORD-20260401-A002','user-1001',1899,'支付宝','success','2026-03-28 16:00:00'),
        ('TXN-003','ORD-20260401-A004','user-1003',52000,'银行卡','success','2026-04-03 10:30:00')""")

    await execute("""INSERT INTO risk_records (user_id, rule_id, rule_desc, score, context, created_at) VALUES
        ('user-1001','R01','新设备登录',15,'login','2026-03-15'),
        ('user-1002','R02','异地IP登录',35,'login','2026-04-01'),
        ('user-1003','R03','大额交易',60,'payment','2026-04-03')""")

    await execute("""INSERT INTO user_profiles (user_id, name, level, balance, phone, email, register_date) VALUES
        ('user-1001','张三','gold',12500.50,'13800138001','zhangsan@example.com','2024-01-15'),
        ('user-1002','李四','silver',3200.00,'13900139002','lisi@example.com','2025-06-01'),
        ('user-1003','王五','platinum',88000.00,'13700137003','wangwu@example.com','2023-03-20')""")

    await execute("""INSERT INTO order_summary (order_id, user_id, username, user_phone, order_status, total_amount, shipping_address, carrier, tracking_no, logistics_status, payment_method, payment_status, payment_amount, products_json, risk_level, risk_score, created_at, updated_at) VALUES
        ('ORD-20260401-A001','user-1001','张三','13800138001','shipped',8999,'北京市朝阳区xxx路1号','顺丰','SF1234567890','运输中','微信支付','paid',8999,'[{"name":"iPhone 15 Pro","qty":1}]','low',12.50,'2026-04-01','2026-04-01'),
        ('ORD-20260401-A002','user-1001','张三','13800138001','delivered',1899,'北京市朝阳区xxx路1号','圆通','YT9876543210','已签收','支付宝','paid',1899,'[{"name":"AirPods Pro","qty":1}]','low',8,'2026-03-28','2026-03-30'),
        ('ORD-20260401-A003','user-1002','李四','13900139002','pending',10499,'上海市浦东新区xxx路2号','','','待发货','银行卡','pending',10499,'[{"name":"MacBook Air M3","qty":1}]','medium',45,'2026-04-02','2026-04-02'),
        ('ORD-20260401-A004','user-1003','王五','13700137003','shipped',52000,'广州市天河区xxx路3号','京东','JD1122334455','配送中','银行卡','paid',52000,'[{"name":"华为Mate 80 Pro","qty":2},{"name":"华为Watch","qty":1}]','high',78,'2026-04-03','2026-04-03')""")

    await execute("""INSERT INTO tickets (ticket_id, order_id, user_id, title, type, priority, reason, status, assignee, resolution, created_at, updated_at) VALUES
        ('TK-20260401-ABCDEF','ORD-20260401-A001','user-1001','iPhone屏幕质量问题申请退款','refund','medium','收到商品后发现屏幕有亮点要求退款','open','','','2026-04-02','2026-04-02'),
        ('TK-20260402-BCDEF1','','user-1002','账户无法登录提示密码错误','complaint','high','已尝试多次无法登录','in_progress','客服小王','','2026-04-03','2026-04-03'),
        ('TK-20260403-CDEF12','ORD-20260401-A004','user-1003','大额订单申请加急处理','general','urgent','订单金额较大希望加急','open','','','2026-04-03','2026-04-03')""")

    # ── 验证 ──
    rows = await fetchall("SHOW TABLES")
    print(f"\n-- Total: {len(rows)} tables --")
    for r in rows:
        t = list(r.values())[0]
        cnt = await fetchall(f"SELECT COUNT(*) AS c FROM {t}")
        print(f"  {t:25s}  {cnt[0]['c']:>4d} rows")

    await close_mysql_pool()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(rebuild())
