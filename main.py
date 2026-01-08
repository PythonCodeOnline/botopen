import os
import logging
import threading
from dotenv import load_dotenv
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ===================== 日志 =====================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("start-gate-drop-queue")

# ===================== 读取 .env =====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
REQUIRED_CHAT_RAW = os.getenv("REQUIRED_CHAT", "").strip()
JOIN_URL = os.getenv("JOIN_URL", "").strip()
SILENT_FOR_NOT_JOINED = os.getenv("SILENT_FOR_NOT_JOINED", "false").strip().lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN 未设置：请在 .env 配置 BOT_TOKEN=xxx")
if not REQUIRED_CHAT_RAW:
    raise RuntimeError("REQUIRED_CHAT 未设置：请在 .env 配置 REQUIRED_CHAT=@xxx 或 -100xxx")

# REQUIRED_CHAT 可能是 -100...，转成 int 更稳
REQUIRED_CHAT = int(REQUIRED_CHAT_RAW) if REQUIRED_CHAT_RAW.lstrip("-").isdigit() else REQUIRED_CHAT_RAW

BTN_RECHECK = "recheck_join"


def build_join_url() -> str:
    """加入链接：优先 JOIN_URL；否则 @username 自动拼 t.me 链接。"""
    if JOIN_URL:
        return JOIN_URL
    if isinstance(REQUIRED_CHAT, str) and REQUIRED_CHAT.startswith("@"):
        return f"https://t.me/{REQUIRED_CHAT[1:]}"
    return ""


def is_joined(status: str) -> bool:
    """判断用户是否算已加入。"""
    return status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED,
    }


async def check_membership(bot, user_id: int) -> bool:
    """调用 getChatMember 检查用户是否在 REQUIRED_CHAT。"""
    member = await bot.get_chat_member(chat_id=REQUIRED_CHAT, user_id=user_id)
    return is_joined(member.status)


async def send_join_prompt(chat_id: int, bot, extra_text: str = ""):
    """提示加入 + 复查按钮。"""
    if SILENT_FOR_NOT_JOINED:
        return

    join_url = build_join_url()
    buttons = []
    if join_url:
        buttons.append([InlineKeyboardButton("✅ 去加入频道/群", url=join_url)])
    buttons.append([InlineKeyboardButton("🔄 我已加入，点我复查", callback_data=BTN_RECHECK)])

    text = "🚫 需要先加入指定频道/群才能使用本机器人。\n加入后点击「我已加入，点我复查」。"
    if extra_text:
        text = extra_text + "\n\n" + text

    await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))


# ===================== 你的原业务逻辑入口（示例占位） =====================
async def business_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    这里写你原来的 /start 业务逻辑（验证通过后才会调用）
    """
    await update.message.reply_text("✅ 校验通过：进入你的业务逻辑（示例占位）")
# =====================================================================


async def start_with_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start：先校验入群，再进入业务逻辑
    """
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        ok = await check_membership(context.bot, uid)
    except TelegramError as e:
        # 不用 Markdown，避免 "Can't parse entities" 类错误
        extra = (
            "⚠️ 无法校验入群状态（可能 REQUIRED_CHAT 配置错误，或 bot 未加入/无权限）。\n"
            f"REQUIRED_CHAT={REQUIRED_CHAT_RAW}\n"
            f"错误：{type(e).__name__}: {str(e)}"
        )
        await send_join_prompt(chat_id, context.bot, extra_text=extra)
        return

    if not ok:
        await send_join_prompt(chat_id, context.bot)
        return

    await business_start(update, context)


async def on_recheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    复查按钮：复查通过后提示用户再发 /start（避免改变你业务流程）
    """
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    chat_id = q.message.chat.id if q.message else None
    if not chat_id:
        return

    try:
        ok = await check_membership(context.bot, uid)
    except TelegramError as e:
        if not SILENT_FOR_NOT_JOINED:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ 复查失败：{type(e).__name__}: {str(e)}")
        return

    if not ok:
        await send_join_prompt(chat_id, context.bot)
        return

    if not SILENT_FOR_NOT_JOINED:
        await context.bot.send_message(chat_id=chat_id, text="🎉 已确认你已加入！请发送 /start 继续。")


# 其它消息（示例）：这里不做门禁，你可以换成自己的业务 handler
async def handle_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass
    #await update.message.reply_text(f"（示例）收到：{update.message.text}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception: %s", context.error)


# ===================== Flask 服务器（支持 Render 健康检查）=====================
flask_app = Flask(__name__)

@flask_app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点 - Render 需要这个来验证服务在运行"""
    return {'status': 'ok'}, 200

def run_flask():
    """在后台线程运行 Flask 服务器"""
    port = int(os.getenv('PORT', '5000'))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

# ===================== 主程序 =====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_with_gate))
    app.add_handler(CallbackQueryHandler(on_recheck, pattern=f"^{BTN_RECHECK}$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_any_text))
    app.add_error_handler(error_handler)

    logger.info("Bot started. REQUIRED_CHAT=%s", REQUIRED_CHAT_RAW)

    # 在后台线程启动 Flask 服务器（为了支持 Render）
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started on port %s", os.getenv('PORT', '5000'))

    # ✅ 关键：丢弃离线期间积压的更新，只处理启动后的新消息
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
