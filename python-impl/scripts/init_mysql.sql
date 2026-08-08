-- ============================================================
-- 智能客服多 Agent 系统 - MySQL 初始化脚本
-- 用法: mysql -u root -p < scripts/init_mysql.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS smart_service
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE smart_service;

SET NAMES utf8mb4;

-- ============================================================
-- 1. users — 用户基础信息
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    user_id    VARCHAR(50)  NOT NULL UNIQUE COMMENT '用户ID',
    username   VARCHAR(100) COMMENT '用户名',
    phone      VARCHAR(20)  COMMENT '手机号',
    created_at DATETIME     NOT NULL COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 2. products — 商品信息
-- ============================================================
CREATE TABLE IF NOT EXISTS products (
    id          INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    product_id  VARCHAR(50)  NOT NULL UNIQUE COMMENT '商品ID',
    name        VARCHAR(200) NOT NULL COMMENT '商品名称',
    category    VARCHAR(50)  NOT NULL COMMENT '商品分类: 手机|配件|笔记本|平板',
    price       FLOAT        NOT NULL COMMENT '单价（元）',
    stock       INT          NOT NULL COMMENT '库存数量',
    description TEXT         COMMENT '商品描述',
    image_url   VARCHAR(500) COMMENT '商品图片URL',
    created_at  DATETIME     NOT NULL COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 3. orders — 订单主表
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id                INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    order_id          VARCHAR(50) NOT NULL UNIQUE COMMENT '订单号',
    user_id           VARCHAR(50) NOT NULL COMMENT '用户ID',
    status            VARCHAR(20) NOT NULL COMMENT '订单状态: pending|paid|shipped|delivered|refunded',
    total_amount      FLOAT       NOT NULL COMMENT '订单总金额（元）',
    shipping_address  TEXT        COMMENT '收货地址',
    created_at        DATETIME    NOT NULL COMMENT '创建时间',
    updated_at        DATETIME    NOT NULL COMMENT '更新时间',
    INDEX idx_orders_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 4. order_items — 订单明细
-- ============================================================
CREATE TABLE IF NOT EXISTS order_items (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    order_id   VARCHAR(50) NOT NULL COMMENT '订单号',
    product_id VARCHAR(50) NOT NULL COMMENT '商品ID',
    quantity   INT         NOT NULL COMMENT '购买数量',
    unit_price FLOAT       NOT NULL COMMENT '单价（元）',
    INDEX idx_oi_order_id (order_id),
    INDEX idx_oi_product_id (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 5. logistics — 物流信息
-- ============================================================
drop table if exists logistics;
CREATE TABLE IF NOT EXISTS logistics (
    id                  INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    order_id            VARCHAR(50)  NOT NULL UNIQUE COLLATE utf8mb4_0900_ai_ci COMMENT '订单号',
    tracking_no         VARCHAR(50)  NOT NULL COLLATE utf8mb4_0900_ai_ci COMMENT '物流单号',
    carrier             VARCHAR(50)  NOT NULL COLLATE utf8mb4_0900_ai_ci COMMENT '快递公司: 顺丰|圆通|中通|京东|韵达|申通',
    status              VARCHAR(20)  NOT NULL COLLATE utf8mb4_0900_ai_ci COMMENT '物流状态: pending|in_transit|delivered',
    origin              VARCHAR(200) COLLATE utf8mb4_0900_ai_ci COMMENT '发货地/仓库',
    destination         VARCHAR(200) COLLATE utf8mb4_0900_ai_ci COMMENT '目的地/收货地址',
    current_location    VARCHAR(200) COLLATE utf8mb4_0900_ai_ci COMMENT '当前所在城市/网点',
    estimated_delivery  DATETIME COMMENT '预计送达时间',
    shipped_at          DATETIME COMMENT '发货时间',
    delivered_at        DATETIME COMMENT '签收时间',
    updated_at          DATETIME COMMENT '物流更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 6. payments — 支付记录
-- ============================================================
CREATE TABLE IF NOT EXISTS payments (
    id              INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    transaction_id  VARCHAR(50) NOT NULL UNIQUE COMMENT '支付流水号',
    order_id        VARCHAR(50) NOT NULL COMMENT '订单号',
    user_id         VARCHAR(50) NOT NULL COMMENT '用户ID',
    amount          FLOAT       NOT NULL COMMENT '支付金额（元）',
    method          VARCHAR(20) NOT NULL COMMENT '支付方式: 微信支付|支付宝|银行卡|Apple Pay',
    status          VARCHAR(20) NOT NULL COMMENT '支付状态: pending|success|failed|refunded',
    created_at      DATETIME    NOT NULL COMMENT '支付时间',
    INDEX idx_pay_order_id (order_id),
    INDEX idx_pay_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 7. risk_records — 风控记录
-- ============================================================
CREATE TABLE IF NOT EXISTS risk_records (
    id         INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    user_id    VARCHAR(50)  NOT NULL COMMENT '用户ID',
    rule_id    VARCHAR(10)  NOT NULL COMMENT '风控规则ID',
    rule_desc  VARCHAR(100) NOT NULL COMMENT '风控规则描述',
    score      INT          NOT NULL COMMENT '风险评分',
    context    VARCHAR(30)  NOT NULL COMMENT '触发场景: login|payment|refund|account_open',
    created_at DATETIME     NOT NULL COMMENT '触发时间',
    INDEX idx_risk_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 8. user_profiles — 用户画像（会员等级/余额/积分）
-- ============================================================
drop table if exists user_profiles;
CREATE TABLE IF NOT EXISTS user_profiles (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    user_id       VARCHAR(32)   NOT NULL UNIQUE COMMENT '用户ID',
    name          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '用户姓名',
    level         VARCHAR(20)   NOT NULL DEFAULT 'silver' COMMENT '会员等级: silver|gold|platinum|diamond',
    balance       DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '账户余额（元）',
    points        INT           NOT NULL DEFAULT 0 COMMENT '账户积分',
    phone         VARCHAR(20)   NOT NULL DEFAULT '' COMMENT '手机号',
    email         VARCHAR(128)  NOT NULL DEFAULT '' COMMENT '邮箱',
    register_date VARCHAR(32)   NOT NULL DEFAULT '' COMMENT '注册日期',
    INDEX idx_profile_level (level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 9. order_summary — 订单汇总宽表（stores.py OrderStore 查询此表）
-- ============================================================
CREATE TABLE IF NOT EXISTS order_summary (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    order_id         VARCHAR(32)   NOT NULL UNIQUE COMMENT '订单号',
    user_id          VARCHAR(32)   NOT NULL COMMENT '用户ID',
    username         VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '用户名',
    user_phone       VARCHAR(20)   NOT NULL DEFAULT '' COMMENT '用户手机号',
    order_status     VARCHAR(20)   NOT NULL DEFAULT 'pending' COMMENT '订单状态: pending|paid|shipped|delivered|refunded',
    total_amount     DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '订单总金额（元）',
    shipping_address VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '收货地址',
    carrier          VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '快递公司',
    tracking_no      VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '物流单号',
    logistics_status VARCHAR(64)   NOT NULL DEFAULT '' COMMENT '物流状态',
    payment_method   VARCHAR(32)   NOT NULL DEFAULT '' COMMENT '支付方式',
    payment_status   VARCHAR(20)   NOT NULL DEFAULT '' COMMENT '支付状态',
    payment_amount   DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '支付金额（元）',
    products_json    TEXT COMMENT '商品列表 JSON',
    risk_level       VARCHAR(10)   NOT NULL DEFAULT '' COMMENT '风控等级',
    risk_score       DECIMAL(5,2)  NOT NULL DEFAULT 0.00 COMMENT '风控评分',
    created_at       VARCHAR(32)   NOT NULL DEFAULT '' COMMENT '创建时间',
    updated_at       VARCHAR(32)   NOT NULL DEFAULT '' COMMENT '更新时间',
    INDEX idx_os_user_id (user_id),
    INDEX idx_os_status (order_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10. tickets — 工单（stores.py TicketStore 操作此表）
-- ============================================================
CREATE TABLE IF NOT EXISTS tickets (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    ticket_id   VARCHAR(32)  NOT NULL UNIQUE COMMENT '工单号: TKT-YYYYMMDD-XXXXXX',
    order_id    VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '关联订单号',
    user_id     VARCHAR(32)  NOT NULL COMMENT '用户ID',
    title       VARCHAR(256) NOT NULL DEFAULT '' COMMENT '工单标题',
    `type`      VARCHAR(20)  NOT NULL COMMENT '工单类型: refund|claim|account_open|account_change|complaint|general',
    priority    VARCHAR(10)  NOT NULL DEFAULT 'medium' COMMENT '优先级: low|medium|high|urgent',
    reason      TEXT COMMENT '工单原因/描述',
    status      VARCHAR(20)  NOT NULL DEFAULT 'open' COMMENT '工单状态: open|in_progress|resolved|closed',
    assignee    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '指派人',
    resolution  TEXT COMMENT '处理结果',
    created_at  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '创建时间',
    updated_at  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '更新时间',
    INDEX idx_tk_user_id (user_id),
    INDEX idx_tk_status (status),
    INDEX idx_tk_type (type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 种子数据
-- ============================================================

TRUNCATE TABLE users;
INSERT INTO users (user_id, username, phone, created_at) VALUES
  ('user-1001', '张三',   '13800138001', '2024-01-15 10:00:00'),
  ('user-1002', '李四',   '13900139002', '2025-06-01 10:00:00'),
  ('user-1003', '王五',   '13700137003', '2023-03-20 10:00:00'),
  ('user-1004', '赵六',   '13600136004', '2024-08-10 10:00:00'),
  ('user-1005', '孙七',   '13500135005', '2025-11-22 10:00:00'),
  ('user-1006', '周八',   '13400134006', '2023-07-05 10:00:00'),
  ('user-1007', '吴九',   '13300133007', '2026-01-03 10:00:00'),
  ('user-1008', '郑十',   '13200132008', '2022-05-18 10:00:00'),
  ('eval-user', '评测员', '13000130000', '2026-06-01 10:00:00');

TRUNCATE TABLE products;
INSERT INTO products (product_id, name, category, price, stock, description, created_at) VALUES
  ('P001', 'iPhone 15 Pro',          '手机',   8999,  50,  'A17 Pro芯片 钛金属边框 4800万像素',              '2026-01-01'),
  ('P002', 'AirPods Pro',            '配件',   1899,  200, '主动降噪 自适应音频 H2芯片',                      '2026-01-01'),
  ('P003', 'MacBook Air M3',         '笔记本', 10499, 30,  'M3芯片 13.6英寸 Liquid Retina屏 18小时续航',     '2026-01-01'),
  ('P004', '华为Mate 80 Pro',        '手机',   7999,  40,  '麒麟芯片 卫星通信 XMAGE影像',                     '2026-01-01'),
  ('P005', '华为Watch GT',           '配件',   2499,  100, '14天续航 心率监测 血氧检测',                       '2026-01-01'),
  ('P006', 'iPad Air M2',            '平板',   5499,  35,  'M2芯片 11英寸 Liquid Retina屏 Apple Pencil支持', '2026-02-01'),
  ('P007', 'Apple Watch Ultra 2',    '配件',   6499,  25,  '49mm钛金属 精准GPS 100米防水',                    '2026-02-01'),
  ('P008', '三星Galaxy S25 Ultra',   '手机',   9999,  30,  '骁龙8 Gen4 2亿像素 钛金属 S Pen',                 '2026-02-01'),
  ('P009', 'Sony WH-1000XM6',        '配件',   2999,  60,  '头戴式降噪 30小时续航 LDAC',                      '2026-03-01'),
  ('P010', 'Dell XPS 16',            '笔记本', 12999, 15,  'Intel Ultra 9 32GB 1TB OLED触控屏',              '2026-03-01');

TRUNCATE TABLE orders;
INSERT INTO orders (order_id, user_id, status, total_amount, shipping_address, created_at, updated_at) VALUES
  ('ORD-20260401-A001', 'user-1001', 'shipped',    8999,  '北京市朝阳区xxx路1号',   '2026-04-01', '2026-04-01'),
  ('ORD-20260401-A002', 'user-1001', 'delivered',  1899,  '北京市朝阳区xxx路1号',   '2026-03-28', '2026-03-30'),
  ('ORD-20260401-A003', 'user-1002', 'pending',   10499,  '上海市浦东新区xxx路2号', '2026-04-02', '2026-04-02'),
  ('ORD-20260401-A004', 'user-1003', 'shipped',   52000,  '广州市天河区xxx路3号',   '2026-04-03', '2026-04-03'),
  ('ORD-20260510-B001', 'user-1004', 'delivered',  5499,  '深圳市南山区xxx路4号',   '2026-05-10', '2026-05-13'),
  ('ORD-20260615-B002', 'user-1005', 'refunded',   7999,  '杭州市西湖区xxx路5号',   '2026-06-15', '2026-06-25'),
  ('ORD-20260701-B003', 'user-1006', 'shipped',    2999,  '成都市武侯区xxx路6号',   '2026-07-01', '2026-07-02'),
  ('ORD-20260711-B004', 'user-1001', 'paid',      12999,  '北京市朝阳区xxx路1号',   '2026-07-11', '2026-07-11'),
  ('ORD-20260720-B005', 'user-1007', 'pending',    6499,  '南京市鼓楼区xxx路7号',   '2026-07-20', '2026-07-20'),
  ('ORD-20260801-B006', 'user-1008', 'shipped',   24999,  '武汉市洪山区xxx路8号',   '2026-08-01', '2026-08-02'),
  ('ORD-20260802-B007', 'user-1002', 'paid',       9999,  '上海市浦东新区xxx路2号', '2026-08-02', '2026-08-02'),
  ('ORD-20260807-E001', 'eval-user', 'paid',      8999,  '重庆市渝北区xxx路10号',  '2026-08-07', '2026-08-07'),
  ('ORD-20260807-E002', 'eval-user', 'shipped',   2999,  '重庆市渝北区xxx路10号',  '2026-08-06', '2026-08-07'),
  ('ORD-20260807-E003', 'eval-user', 'pending',   5499,  '重庆市渝北区xxx路10号',  '2026-08-07', '2026-08-07'),
  ('ORD-20260807-E004', 'eval-user', 'delivered',  2499,  '重庆市渝北区xxx路10号',  '2026-07-28', '2026-08-01');

TRUNCATE TABLE order_items;
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
  ('ORD-20260401-A001', 'P001', 1, 8999),
  ('ORD-20260401-A002', 'P002', 1, 1899),
  ('ORD-20260401-A003', 'P003', 1, 10499),
  ('ORD-20260401-A004', 'P004', 2, 7999),
  ('ORD-20260401-A004', 'P005', 1, 2499),
  ('ORD-20260510-B001', 'P006', 1, 5499),
  ('ORD-20260615-B002', 'P004', 1, 7999),
  ('ORD-20260701-B003', 'P009', 1, 2999),
  ('ORD-20260711-B004', 'P010', 1, 12999),
  ('ORD-20260720-B005', 'P007', 1, 6499),
  ('ORD-20260801-B006', 'P003', 1, 10499),
  ('ORD-20260801-B006', 'P008', 1, 9999),
  ('ORD-20260801-B006', 'P005', 1, 2499),
  ('ORD-20260802-B007', 'P008', 1, 9999),
  ('ORD-20260807-E001', 'P001', 1, 8999),
  ('ORD-20260807-E002', 'P009', 1, 2999),
  ('ORD-20260807-E003', 'P006', 1, 5499),
  ('ORD-20260807-E004', 'P005', 1, 2499);

TRUNCATE TABLE logistics;
INSERT INTO logistics (order_id, tracking_no, carrier, status, origin, destination, current_location, estimated_delivery, shipped_at, delivered_at) VALUES
  ('ORD-20260401-A001', 'SF1234567890',  '顺丰', 'in_transit', '上海仓',   '北京市朝阳区', '北京顺义分拣中心',     '2026-04-03 18:00:00', '2026-04-01 12:00:00', NULL),
  ('ORD-20260401-A002', 'YT9876543210',  '圆通', 'delivered',  '深圳仓',   '北京市朝阳区', '已签收（本人）',        NULL,                  '2026-03-28 14:00:00', '2026-03-30 10:00:00'),
  ('ORD-20260401-A004', 'JD1122334455',  '京东', 'in_transit', '广州仓',   '广州市天河区', '广州天河区派送站',     '2026-04-04 12:00:00', '2026-04-03 09:00:00', NULL),
  ('ORD-20260510-B001', 'SF1234567891',  '顺丰', 'delivered',  '北京仓',   '深圳市南山区', '已签收（前台代收）',    NULL,                  '2026-05-10 15:00:00', '2026-05-13 11:00:00'),
  ('ORD-20260615-B002', 'YT9876543211',  '圆通', 'delivered',  '上海仓',   '杭州市西湖区', '已签收（本人）',        NULL,                  '2026-06-15 10:00:00', '2026-06-18 09:00:00'),
  ('ORD-20260701-B003', 'ZTO1234567890', '中通', 'in_transit', '成都仓',   '成都市武侯区', '成都武侯区网点（派送中）','2026-07-03 20:00:00', '2026-07-01 16:00:00', NULL),
  ('ORD-20260801-B006', 'JD1122334456',  '京东', 'in_transit', '武汉仓',   '武汉市洪山区', '武汉洪山区珞南街道网点', '2026-08-03 14:00:00', '2026-08-01 08:00:00', NULL),
  ('ORD-20260807-E002', 'SF1234567892',  '顺丰', 'in_transit', '成都仓',   '重庆市渝北区', '重庆渝北区网点（派送中）', '2026-08-08 18:00:00', '2026-08-07 09:00:00', NULL),
  ('ORD-20260807-E004', 'YT9876543212',  '圆通', 'delivered',  '上海仓',   '重庆市渝北区', '已签收（本人）',          NULL,                  '2026-07-28 14:00:00', '2026-08-01 10:00:00');

TRUNCATE TABLE payments;
INSERT INTO payments (transaction_id, order_id, user_id, amount, method, status, created_at) VALUES
  ('TXN-001', 'ORD-20260401-A001', 'user-1001', 8999,   '微信支付', 'success',  '2026-04-01 10:05:00'),
  ('TXN-002', 'ORD-20260401-A002', 'user-1001', 1899,   '支付宝',   'success',  '2026-03-28 16:00:00'),
  ('TXN-003', 'ORD-20260401-A004', 'user-1003', 52000,  '银行卡',   'success',  '2026-04-03 10:30:00'),
  ('TXN-004', 'ORD-20260510-B001', 'user-1004', 5499,   '微信支付', 'success',  '2026-05-10 10:05:00'),
  ('TXN-005', 'ORD-20260615-B002', 'user-1005', 7999,   '支付宝',   'refunded', '2026-06-15 14:00:00'),
  ('TXN-006', 'ORD-20260701-B003', 'user-1006', 2999,   '微信支付', 'success',  '2026-07-01 10:30:00'),
  ('TXN-007', 'ORD-20260711-B004', 'user-1001', 12999,  '银行卡',   'success',  '2026-07-11 09:00:00'),
  ('TXN-008', 'ORD-20260801-B006', 'user-1008', 24999,  '银行卡',   'success',  '2026-08-01 08:00:00'),
  ('TXN-009', 'ORD-20260807-E001', 'eval-user', 8999,   '微信支付', 'success',  '2026-08-07 10:00:00'),
  ('TXN-010', 'ORD-20260807-E002', 'eval-user', 2999,   '支付宝',   'success',  '2026-08-06 15:00:00'),
  ('TXN-011', 'ORD-20260807-E004', 'eval-user', 2499,   '微信支付', 'success',  '2026-07-28 11:00:00');

TRUNCATE TABLE risk_records;
INSERT INTO risk_records (user_id, rule_id, rule_desc, score, context, created_at) VALUES
  ('user-1001', 'R01', '新设备登录',       15, 'login',         '2026-03-15'),
  ('user-1002', 'R02', '异地IP登录',       35, 'login',         '2026-04-01'),
  ('user-1003', 'R03', '大额交易',         60, 'payment',       '2026-04-03'),
  ('user-1004', 'R04', '频繁退货',         45, 'refund',        '2026-05-20'),
  ('user-1005', 'R05', '异常退款申请',     70, 'refund',        '2026-06-16'),
  ('user-1006', 'R01', '新设备登录',       15, 'login',         '2026-07-01'),
  ('user-1007', 'R06', '账户信息变更',     25, 'account_open',  '2026-07-21'),
  ('user-1008', 'R03', '大额交易',         55, 'payment',       '2026-08-01'),
  ('user-1001', 'R07', '连续下单检测',     20, 'payment',       '2026-07-12'),
  ('user-1003', 'R08', '跨境交易风控',     80, 'payment',       '2026-05-01');

TRUNCATE TABLE user_profiles;
INSERT INTO user_profiles (user_id, name, level, balance, points, phone, email, register_date) VALUES
  ('user-1001', '张三', 'gold',      12500.50, 5200,  '13800138001', 'zhangsan@example.com',  '2024-01-15'),
  ('user-1002', '李四', 'silver',     3200.00, 800,   '13900139002', 'lisi@example.com',      '2025-06-01'),
  ('user-1003', '王五', 'platinum',  88000.00, 35000, '13700137003', 'wangwu@example.com',   '2023-03-20'),
  ('user-1004', '赵六', 'gold',      18600.00, 7200,  '13600136004', 'zhaoliu@example.com',   '2024-08-10'),
  ('user-1005', '孙七', 'silver',      850.00, 200,   '13500135005', 'sunqi@example.com',     '2025-11-22'),
  ('user-1006', '周八', 'gold',      28500.00, 11800, '13400134006', 'zhouba@example.com',    '2023-07-05'),
  ('user-1007', '吴九', 'silver',     1200.00, 350,   '13300133007', 'wujiu@example.com',     '2026-01-03'),
  ('user-1008', '郑十', 'diamond',  120000.00, 50000, '13200132008', 'zhengshi@example.com',  '2022-05-18'),
  ('eval-user', '评测员', 'gold',     8800.00, 3200,  '13000130000', 'eval-user@example.com',  '2026-06-01');

TRUNCATE TABLE order_summary;
INSERT INTO order_summary (order_id, user_id, username, user_phone, order_status, total_amount, shipping_address, carrier, tracking_no, logistics_status, payment_method, payment_status, payment_amount, products_json, risk_level, risk_score, created_at, updated_at) VALUES
  ('ORD-20260401-A001', 'user-1001', '张三', '13800138001', 'shipped',    8999,  '北京市朝阳区xxx路1号',   '顺丰', 'SF1234567890',  '运输中',   '微信支付', 'paid',     8999,  '[{"name":"iPhone 15 Pro","qty":1}]',                                     'low',    12.50, '2026-04-01', '2026-04-01'),
  ('ORD-20260401-A002', 'user-1001', '张三', '13800138001', 'delivered',  1899,  '北京市朝阳区xxx路1号',   '圆通', 'YT9876543210',  '已签收',   '支付宝',   'paid',     1899,  '[{"name":"AirPods Pro","qty":1}]',                                       'low',    8,     '2026-03-28', '2026-03-30'),
  ('ORD-20260401-A003', 'user-1002', '李四', '13900139002', 'pending',   10499,  '上海市浦东新区xxx路2号', '',     '',              '待发货',   '银行卡',   'pending', 10499,  '[{"name":"MacBook Air M3","qty":1}]',                                    'medium', 45,    '2026-04-02', '2026-04-02'),
  ('ORD-20260401-A004', 'user-1003', '王五', '13700137003', 'shipped',   52000,  '广州市天河区xxx路3号',   '京东', 'JD1122334455',  '配送中',   '银行卡',   'paid',    52000,  '[{"name":"华为Mate 80 Pro","qty":2},{"name":"华为Watch","qty":1}]',    'high',   78,    '2026-04-03', '2026-04-03'),
  ('ORD-20260510-B001', 'user-1004', '赵六', '13600136004', 'delivered',  5499,  '深圳市南山区xxx路4号',   '顺丰', 'SF1234567891',  '已签收',   '微信支付', 'paid',     5499,  '[{"name":"iPad Air M2","qty":1}]',                                       'low',    10,    '2026-05-10', '2026-05-13'),
  ('ORD-20260615-B002', 'user-1005', '孙七', '13500135005', 'refunded',   7999,  '杭州市西湖区xxx路5号',   '圆通', 'YT9876543211',  '已退回',   '支付宝',   'refunded', 7999,  '[{"name":"华为Mate 80 Pro","qty":1}]',                                   'high',   72,    '2026-06-15', '2026-06-25'),
  ('ORD-20260701-B003', 'user-1006', '周八', '13400134006', 'shipped',    2999,  '成都市武侯区xxx路6号',   '中通', 'ZTO1234567890', '运输中',   '微信支付', 'paid',     2999,  '[{"name":"Sony WH-1000XM6","qty":1}]',                                   'low',    15,    '2026-07-01', '2026-07-02'),
  ('ORD-20260711-B004', 'user-1001', '张三', '13800138001', 'paid',      12999,  '北京市朝阳区xxx路1号',   '',     '',              '待发货',   '银行卡',   'paid',    12999,  '[{"name":"Dell XPS 16","qty":1}]',                                       'low',    18,    '2026-07-11', '2026-07-11'),
  ('ORD-20260720-B005', 'user-1007', '吴九', '13300133007', 'pending',    6499,  '南京市鼓楼区xxx路7号',   '',     '',              '待付款',   '银行卡',   'pending',  6499,  '[{"name":"Apple Watch Ultra 2","qty":1}]',                               'low',    5,     '2026-07-20', '2026-07-20'),
  ('ORD-20260801-B006', 'user-1008', '郑十', '13200132008', 'shipped',   24999,  '武汉市洪山区xxx路8号',   '京东', 'JD1122334456',  '配送中',   '银行卡',   'paid',    24999,  '[{"name":"MacBook Air M3","qty":1},{"name":"三星S25 Ultra","qty":1}]', 'medium', 42,    '2026-08-01', '2026-08-02'),
  ('ORD-20260802-B007', 'user-1002', '李四', '13900139002', 'paid',       9999,  '上海市浦东新区xxx路2号', '',     '',              '待发货',   '微信支付', 'paid',     9999,  '[{"name":"三星Galaxy S25 Ultra","qty":1}]',                              'medium', 35,    '2026-08-02', '2026-08-02'),
  ('ORD-20260807-E001', 'eval-user', '评测员', '13000130000', 'paid',       8999,  '重庆市渝北区xxx路10号',  '',     '',              '待发货',   '微信支付', 'paid',     8999,  '[{"name":"iPhone 15 Pro","qty":1}]',                                     'low',    8,     '2026-08-07', '2026-08-07'),
  ('ORD-20260807-E002', 'eval-user', '评测员', '13000130000', 'shipped',    2999,  '重庆市渝北区xxx路10号',  '顺丰', 'SF1234567892',  '运输中',   '支付宝',   'paid',     2999,  '[{"name":"Sony WH-1000XM6","qty":1}]',                                   'low',    10,    '2026-08-06', '2026-08-07'),
  ('ORD-20260807-E003', 'eval-user', '评测员', '13000130000', 'pending',    5499,  '重庆市渝北区xxx路10号',  '',     '',              '待付款',   '',         'pending',  5499,  '[{"name":"iPad Air M2","qty":1}]',                                       'low',    5,     '2026-08-07', '2026-08-07'),
  ('ORD-20260807-E004', 'eval-user', '评测员', '13000130000', 'delivered',  2499,  '重庆市渝北区xxx路10号',  '圆通', 'YT9876543212',  '已签收',   '微信支付', 'paid',     2499,  '[{"name":"华为Watch GT","qty":1}]',                                      'low',    6,     '2026-07-28', '2026-08-01');

TRUNCATE TABLE tickets;
INSERT INTO tickets (ticket_id, order_id, user_id, title, type, priority, reason, status, assignee, resolution, created_at, updated_at) VALUES
  ('TKT-20260401-ABCDEF', 'ORD-20260401-A001', 'user-1001', 'iPhone屏幕质量问题申请退款',         'refund',        'medium', '收到商品后发现屏幕有亮点要求退款',                              'open',        '',        '',        '2026-04-02', '2026-04-02'),
  ('TKT-20260402-BCDEF1', '',                  'user-1002', '账户无法登录提示密码错误',           'complaint',     'high',   '已尝试多次无法登录',                                            'in_progress', '客服小王', '',        '2026-04-03', '2026-04-03'),
  ('TKT-20260403-CDEF12', 'ORD-20260401-A004', 'user-1003', '大额订单申请加急处理',               'general',       'urgent', '订单金额较大希望加急',                                          'open',        '',        '',        '2026-04-03', '2026-04-03'),
  ('TKT-20260520-00001', 'ORD-20260510-B001', 'user-1004', 'iPad屏幕出现坏点要求换货',           'claim',         'medium', 'iPad屏幕中央有3个坏点影响正常使用',                              'resolved',    '客服小李', '已换货',  '2026-05-20', '2026-05-28'),
  ('TKT-20260616-00002', 'ORD-20260615-B002', 'user-1005', '华为手机信号差申请退货退款',         'refund',        'high',   '手机在地下室和电梯内无信号已申请退货',                           'resolved',    '客服小张', '已退款',  '2026-06-16', '2026-06-26'),
  ('TKT-20260702-ABCDE3', '',                  'user-1006', '要求修改账户绑定的手机号',           'account_change','medium', '原手机号已停用需要更换为新号138xxxx',                             'open',        '',        '',        '2026-07-02', '2026-07-02'),
  ('TKT-20260712-00003', 'ORD-20260711-B004', 'user-1001', '笔记本风扇噪音大申请检测',           'complaint',     'medium', 'Dell XPS 16开机后风扇持续高速运转噪音严重',                      'in_progress', '客服小赵', '',        '2026-07-12', '2026-07-13'),
  ('TKT-20260721-ABCDE4', '',                  'user-1007', '申请开通企业账户',                   'account_open',  'low',    '个体工商户需要开通企业账户用于批量采购',                         'open',        '',        '',        '2026-07-21', '2026-07-21'),
  ('TKT-20260802-00004', 'ORD-20260801-B006', 'user-1008', '加急处理多件商品订单',               'general',       'urgent', '订单包含多件商品希望优先配货加急配送',                           'open',        '',        '',        '2026-08-02', '2026-08-02'),
  ('TKT-20260802-ABCDE5', '',                  'user-1003', '投诉客服态度差要求处理',             'complaint',     'high',   '与客服沟通退款事宜时客服态度恶劣数次打断用户发言',              'open',        '主管-陈', '',        '2026-08-02', '2026-08-03'),
  ('TKT-20260807-EVAL01', 'ORD-20260807-E002', 'eval-user', '耳机左耳无声申请换货检测',           'claim',         'medium', 'Sony WH-1000XM6 左耳完全无声已尝试重置无效要求检测换货',       'in_progress', '客服小李', '',        '2026-08-07', '2026-08-07'),
  ('TKT-20260807-EVAL02', '',                  'eval-user', '咨询会员升级规则',                   'general',       'low',    '当前gold会员想了解升级到platinum的条件和权益差异',              'open',        '',        '',        '2026-08-07', '2026-08-07');
