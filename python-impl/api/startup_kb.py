# ============================================================
# 知识库启动初始化 — 后台线程，增量更新，不阻塞 HTTP 服务
# ============================================================

from __future__ import annotations

import json
import logging
import asyncio
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEXED_FILE = ".kb_indexed.json"


def _load_indexed_manifest(kb_dir: str) -> dict[str, float]:
    """加载已索引文件清单 {相对路径: 修改时间戳}。"""
    manifest_path = Path(kb_dir) / _INDEXED_FILE
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read kb manifest: %s", exc)
        return {}


def _save_indexed_manifest(kb_dir: str, manifest: dict[str, float]) -> None:
    """保存已索引文件清单。"""
    manifest_path = Path(kb_dir) / _INDEXED_FILE
    try:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to write kb manifest: %s", exc)


def _scan_kb_files(kb_dir: str) -> dict[str, float]:
    """扫描知识库目录，返回 {相对路径: 修改时间戳}。支持 txt/md/pdf/docx/xlsx。"""
    kb_path = Path(kb_dir)
    if not kb_path.exists():
        return {}
    result: dict[str, float] = {}
    for ext in ("*.txt", "*.md", "*.pdf", "*.docx", "*.xlsx"):
        for f in sorted(kb_path.rglob(ext)):
            rel = str(f.relative_to(kb_path)).replace("\\", "/")
            result[rel] = f.stat().st_mtime
    return result


def initialize_knowledge_base_background(settings, knowledge_store) -> None:
    """后台线程增量更新知识库索引。

    策略：
    1. 扫描 kb_dir 中所有 .txt 文件，与 .kb_indexed.json 比对
    2. 仅加载新增和修改过的文件（增量更新）
    3. 索引为空 + kb_seed_on_startup → 额外加载内置示例文档
    4. 更新 .kb_indexed.json 清单
    """

    def _run() -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            kb_dir = settings.kb_dir
            current_files = _scan_kb_files(kb_dir)
            indexed = _load_indexed_manifest(kb_dir)

            # 找出新增和修改过的文件
            new_or_changed: dict[str, float] = {}
            for rel_path, mtime in current_files.items():
                if rel_path not in indexed or indexed[rel_path] < mtime:
                    new_or_changed[rel_path] = mtime

            if not new_or_changed and knowledge_store.document_count > 0:
                logger.info("Knowledge base: all %d files up to date (%d docs), no update needed",
                           len(current_files), knowledge_store.document_count)
                # 首次运行无历史数据时仍需加载种子
                if not indexed and settings.kb_seed_on_startup:
                    loop.run_until_complete(_load_seed_documents(knowledge_store))
                return

            if new_or_changed:
                logger.info("Knowledge base: %d new/changed files detected, loading incrementally...",
                           len(new_or_changed))
                files_list = [str(Path(kb_dir) / f) for f in new_or_changed]
                count = loop.run_until_complete(
                    knowledge_store.load_files(files_list, base_dir=kb_dir)
                )
                logger.info("Knowledge base: %d new chunks indexed from %d files",
                           count, len(new_or_changed))
                # 更新清单
                indexed.update(new_or_changed)
                _save_indexed_manifest(kb_dir, indexed)
            else:
                logger.info("Knowledge base: no new files found, %d docs exist", knowledge_store.document_count)

            # 首次运行（无历史清单 + 索引为空）→ 加载种子
            if not indexed and knowledge_store.document_count == 0 and settings.kb_seed_on_startup:
                logger.info("Loading seed documents ...")
                loop.run_until_complete(_load_seed_documents(knowledge_store))
                logger.info("Seed documents loaded: %d total", knowledge_store.document_count)

        except Exception as exc:
            logger.error("Knowledge base initialization failed: %s", exc)
        finally:
            if loop is not None:
                loop.close()

    thread = threading.Thread(target=_run, name="kb-startup", daemon=True)
    thread.start()
    logger.info("Knowledge base background initialization started")


async def _load_seed_documents(knowledge_store) -> None:
    """加载内置示例文档，覆盖智能客服常见业务场景。"""
    seeds = [
        # -- 退货退款 --
        {
            "content": "退货政策：自签收之日起7天内可申请无理由退货，15天内如有质量问题可换货。退款将在收到退货后3-5个工作日原路返回。退回商品需保持原包装完整，配件齐全。",
            "source": "退货政策",
        },
        {
            "content": "退款时效：支付宝/微信支付退款到账时间为1-3个工作日，银行卡退款到账时间为3-7个工作日。如超时未到账，请提供订单号联系客服核查。退款金额为实际支付金额，优惠券部分不予退还。",
            "source": "退款时效",
        },
        {
            "content": "换货流程：商品存在质量问题可在15天内申请换货。换货无需支付额外运费，平台承担往返运费。换货商品发出后，物流单号将短信通知您。换货仅支持同款同色商品，不支持更换其他型号。",
            "source": "换货流程",
        },
        # -- 物流配送 --
        {
            "content": "物流查询：您可以在订单详情页查看物流信息，或提供订单号联系客服查询。标准配送3-5个工作日，加急配送1-2个工作日。偏远地区（新疆、西藏、青海等）配送时间可能延长至5-10个工作日。",
            "source": "物流查询",
        },
        {
            "content": "配送范围：目前覆盖全国31个省市区（不含港澳台）。乡镇及农村地区配送时间可能延长2-3个工作日。生鲜商品仅支持部分城市次日达，具体以商品页面标注为准。",
            "source": "配送范围",
        },
        # -- 账户安全 --
        {
            "content": "账户安全：建议开启两步验证，定期修改密码。如发现异常登录，请立即联系客服冻结账户。不要在公共设备上保存密码，不要向任何人透露验证码。密码长度至少8位，需包含字母、数字和特殊字符。",
            "source": "账户安全",
        },
        {
            "content": "账户注销：用户可在线申请账户注销。注销前需确保无进行中的订单、无未结清款项、无未完成的工单。注销申请提交后，有15天冷静期，期间可随时取消注销。冷静期结束后账户将永久删除，数据不可恢复。",
            "source": "账户注销",
        },
        # -- 会员权益 --
        {
            "content": "会员等级体系：平台会员分为四个等级——银卡会员(silver)、金卡会员(gold)、白金卡会员(platinum)、钻石卡会员(diamond)。等级根据近12个月累计消费金额评定：银卡0-5000元，金卡5000-20000元，白金卡20000-50000元，钻石卡50000元以上。",
            "source": "会员等级",
        },
        {
            "content": "会员权益：金卡会员享全场98折、生日月双倍积分；白金卡会员享全场95折、专属客服通道、免费加急配送；钻石卡会员享全场9折、24小时专属客服经理、每年2次免费退换货、线下活动优先参与权。积分可按100:1比例抵扣现金。",
            "source": "会员权益",
        },
        # -- 支付方式 --
        {
            "content": "支持的支付方式：微信支付、支付宝、银联云闪付、各大银行借记卡/信用卡（工商银行、建设银行、招商银行、中国银行等）、Apple Pay。部分商品支持花呗分期和白条分期，分期期数为3/6/12期，手续费以支付页面显示为准。",
            "source": "支付方式",
        },
        # -- 投诉工单 --
        {
            "content": "投诉处理流程：用户可通过在线客服、客服电话或APP内提交投诉工单。投诉提交后，客服将在2小时内首次响应，24小时内给出处理方案。投诉类型包括：商品质量问题、物流延误、服务态度、虚假宣传等。投诉处理完成后，用户可对处理结果进行评价。",
            "source": "投诉处理",
        },
        # -- 开户注册 --
        {
            "content": "开户注册流程：下载APP后点击注册，输入手机号获取验证码，设置登录密码和支付密码。完成实名认证需上传身份证正反面照片并进行人脸识别。实名认证审核通常在10分钟内完成。一个身份证号仅可注册一个账户。",
            "source": "开户注册",
        },
        # -- 风控规则 --
        {
            "content": "交易风控规则：单笔交易金额超过5000元触发风控审核；单日累计交易超过20000元触发加强审核。高风险地区IP或新注册账户（注册不满30天）的大额交易将自动拦截。用户可联系客服提交身份验证材料解除风控限制。",
            "source": "风控规则",
        },
        # -- 隐私政策 --
        {
            "content": "隐私与数据保护：平台严格遵守个人信息保护法。用户的姓名、手机号、身份证号、银行卡号等敏感信息采用加密存储。用户可随时在设置中导出或删除个人数据。平台不会向第三方提供用户个人信息，法律法规另有规定的除外。",
            "source": "隐私政策",
        },
    ]

    for seed in seeds:
        await knowledge_store.add_document(
            content=seed["content"],
            source=seed["source"],
        )
