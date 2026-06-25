import json
import logging
import os
from datetime import datetime
from pathlib import Path

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    # Lets you keep secrets in a local ".env" file during development
    # instead of typing them into your terminal every time. Safe to leave
    # this here even in production — it just does nothing if no .env exists.
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# CONFIG — these now come ONLY from environment variables, never hardcoded.
# Locally: create a ".env" file (see .env.example) in this same folder.
# On Railway/Render: set these under your project's "Variables" tab.
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
_admin_id_raw = os.environ.get("ADMIN_CHAT_ID")
ADMIN_CHAT_ID = int(_admin_id_raw) if _admin_id_raw else 0

import requests

# Live USD→NGN rate is fetched automatically (see get_usd_to_ngn_rate below).
# This is only used if the API can't be reached when the bot starts.
FALLBACK_USD_TO_NGN_RATE = 1370

# How long to keep a fetched rate before refreshing it again (in seconds).
RATE_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

_rate_cache = {"rate": None, "fetched_at": 0}

PRODUCTS = {
    "core": {
        "name": "The Core Course",
        "price": "$50 (one-time)",
        "amount_for_record": "$50",
        "amount_usd": 50,
        "benefits": [
            "Complete TRU$T strategy framework, step by step",
            "How to identify setups with confidence",
            "Risk management rules that keep you in the game",
            "Lifetime access to this course",
        ],
        "delivery_text": (
            "🎉 Payment confirmed! Here's your access to *The Core Course*:\n\n"
            "https://t.me/+9pdIkT-xXi5jYmQ0\n\n"
            "Welcome in — if you have any questions, just message me here."
        ),
    },
    "full": {
        "name": "The Full System Membership",
        "price": "$197 to join, then $25/month to stay active",
        "amount_for_record": "$197",
        "amount_usd": 197,
        "renewal_usd": 25,
        "benefits": [
            "Everything in the Core Course",
            "Daily Trade idea/analysis",
            "Advanced setups beyond the basics",
            "Full backtest breakdowns across market conditions",
            "Trade Journal Template included free",
            "Weekly market breakdown videos, ongoing",
            "Trade review videos from my own trading",
            "Private Discord with other serious traders",
            "Growing content archive — cancel anytime",
        ],
        "delivery_text": (
            "🎉 Payment confirmed! Here's your access to *The Full System*:\n\n"
            "👉 Course material: https://t.me/+9pdIkT-xXi5jYmQ0\n"
            "👉 Private Discord: https://discord.gg/v6nNfyCQ3\n\n"
            "Remember: $25/month keeps your membership active. I'll follow up "
            "with you for renewals — welcome in!"
        ),
    },
}

def bank_details_text(product: dict) -> str:
    join_line = f"{product['amount_for_record']} ({to_ngn(product['amount_usd'])})"
    renewal_line = ""
    if "renewal_usd" in product:
        renewal_line = f"\nMonthly renewal: ${product['renewal_usd']} ({to_ngn(product['renewal_usd'])})"

    return (
        "*Bank Transfer Details*\n\n"
        "Account Name: `Chidiebube Emeka`\n"
        "Bank: `Nombank`\n"
        "Account Number: `9064721908`\n"
        # "SWIFT/Routing: `XXXXXX` _(if international)_\n\n"
        f"Amount to send: *{join_line}*{renewal_line}\n\n"
        "_(Naira amount is based on the current exchange rate and may shift "
        "slightly — send the equivalent as closely as you can.)_\n\n"
        "Please transfer the exact amount, then tap *I've Paid* below and "
        "send a screenshot of the transfer as proof."
    )


# Used only to power the "Copy account number" button below — keep this in
# sync with the Account Number shown in bank_details_text() above.
BANK_ACCOUNT_NUMBER = "9064721908"


PAYMENT_METHODS = {
    "bank": {
        "label": "🏦 Bank Transfer",
    },
    "crypto": {
        "label": "🪙 Crypto",
    },
}

# Fill in your real wallet addresses below — one per network.
CRYPTO_NETWORKS = {
    "btc": {
        "label": "₿ BTC",
        "network_name": "Bitcoin (BTC)",
        "address": "1AUQVqsVvbfLqcWcy8BNVZM2qdTwkC1EiF",
    },
    "usdt_trc20": {
        "label": "💵 USDT (TRC20)",
        "network_name": "Tron (TRC20)",
        "address": "TXMGT3DmAw1Eaxfpmb8ReULQmBag1q4zhU",
    },
    "usdt_erc20": {
        "label": "💵 USDT (ERC20)",
        "network_name": "Ethereum (ERC20)",
        "address": "0xf4e2ede122edeabaca0347ae84a65283364a4274",
    },
    "usdt_bep20": {
        "label": "💵 USDT (BEP20)",
        "network_name": "BNB Smart Chain (BEP20)",
        "address": "0xf4e2ede122edeabaca0347ae84a65283364a4274",
    },
}


def crypto_details_text(network: dict) -> str:
    return (
        "*Crypto Payment Details*\n\n"
        f"Network: `{network['network_name']}`\n"
        f"Wallet Address: `{network['address']}`\n\n"
        "Please send the exact USD-equivalent amount, then tap *I've Paid* "
        "below and send a screenshot or transaction hash as proof."
    )


def get_method_label(method_key: str) -> str:
    """Returns a human-readable label for any payment method, including
    crypto sub-networks like 'crypto_btc' or 'crypto_usdt_trc20'."""
    if method_key.startswith("crypto_"):
        network_key = method_key.split("_", 1)[1]
        return f"🪙 Crypto — {CRYPTO_NETWORKS[network_key]['label']}"
    return PAYMENT_METHODS[method_key]["label"]

DATA_FILE = Path(__file__).parent / "orders.json"


def get_usd_to_ngn_rate() -> float:
    """
    Returns the current USD->NGN rate, fetched from a free public API and
    cached for RATE_CACHE_TTL_SECONDS so we're not hitting the API on every
    single message. Falls back to FALLBACK_USD_TO_NGN_RATE if the request
    fails or the API is unreachable (e.g. no internet at that moment).
    """
    now = datetime.now().timestamp()
    if _rate_cache["rate"] and (now - _rate_cache["fetched_at"] < RATE_CACHE_TTL_SECONDS):
        return _rate_cache["rate"]

    try:
        # Free, no API key required: https://www.exchangerate-api.com/docs/free
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=6)
        resp.raise_for_status()
        data = resp.json()
        rate = data["rates"]["NGN"]
        _rate_cache["rate"] = rate
        _rate_cache["fetched_at"] = now
        logger.info(f"Fetched live USD->NGN rate: {rate}")
        return rate
    except Exception as e:
        logger.warning(f"Could not fetch live exchange rate, using fallback. Error: {e}")
        # Keep using whatever we last successfully fetched, if anything;
        # otherwise use the hardcoded fallback.
        return _rate_cache["rate"] or FALLBACK_USD_TO_NGN_RATE


def to_ngn(usd_amount: float) -> str:
    """Convert a USD amount to a formatted Naira string, e.g. ₦68,500."""
    rate = get_usd_to_ngn_rate()
    ngn = usd_amount * rate
    return f"₦{ngn:,.0f}"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# user_id -> {"product": "core"/"full", "method": "bank"/"crypto", "stage": "..."}
user_state = {}


# ---------------------------------------------------------------------------
# ORDER STORAGE (simple JSON file, no database needed)
# ---------------------------------------------------------------------------
def load_orders():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return []


def save_order(order: dict):
    orders = load_orders()
    orders.append(order)
    DATA_FILE.write_text(json.dumps(orders, indent=2))


def update_order_status(order_id: str, status: str):
    orders = load_orders()
    for o in orders:
        if o["order_id"] == order_id:
            o["status"] = status
    DATA_FILE.write_text(json.dumps(orders, indent=2))


# ---------------------------------------------------------------------------
# /start — supports deep links like t.me/yourbot?start=core or ?start=full
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = update.effective_user.id

    if args and args[0] in PRODUCTS:
        await show_product(update, context, args[0])
    else:
        keyboard = [
            [InlineKeyboardButton(PRODUCTS["core"]["name"], callback_data="product_core")],
            [InlineKeyboardButton(PRODUCTS["full"]["name"], callback_data="product_full")],
        ]
        await update.message.reply_text(
            "Welcome to *The System*. Choose what you'd like to join:\n\n"
            "🟢 *The Core Course* — $50 one-time\n"
            "The complete strategy framework, taught step by step.\n\n"
            "🟣 *The Full System Membership* — $197 to join, then $25/mo\n"
            "Everything in Core, plus advanced setups, weekly breakdowns, "
            "trade reviews, and the private Discord.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_key: str):
    product = PRODUCTS[product_key]
    user_id = update.effective_user.id
    user_state[user_id] = {"product": product_key, "stage": "choosing_method"}

    keyboard = [
        [InlineKeyboardButton(m["label"], callback_data=f"pay_{key}")]
        for key, m in PAYMENT_METHODS.items()
    ]
    benefits_text = "\n".join(f"✅ {b}" for b in product["benefits"])
    text = (
        f"*{product['name']}*\n"
        f"Price: {product['price']}\n\n"
        f"*What you get:*\n{benefits_text}\n\n"
        f"How would you like to pay?"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Button presses
# ---------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # --- product chosen from the welcome menu ---
    if data.startswith("product_"):
        product_key = data.split("_", 1)[1]
        await show_product(update, context, product_key)
        return

    # --- payment method chosen ---
    if data.startswith("pay_"):
        method_key = data.split("_", 1)[1]

        # crypto needs a second step: pick which network/coin
        if method_key == "crypto":
            state = user_state.get(user_id, {})
            state["stage"] = "choosing_crypto_network"
            user_state[user_id] = state

            keyboard = [
                [InlineKeyboardButton(net["label"], callback_data=f"cryptonet_{key}")]
                for key, net in CRYPTO_NETWORKS.items()
            ]
            await query.message.reply_text(
                "Which coin/network would you like to pay with?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        state = user_state.get(user_id, {})
        state["method"] = method_key
        state["stage"] = "awaiting_proof_button"
        user_state[user_id] = state

        product = PRODUCTS[state["product"]]
        details = bank_details_text(product)

        keyboard = [
            [InlineKeyboardButton(
                "📋 Copy Account Number",
                copy_text=CopyTextButton(text=BANK_ACCOUNT_NUMBER),
            )],
            [InlineKeyboardButton("✅ I've Paid", callback_data="paid")],
        ]
        await query.message.reply_text(
            details, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )
        return

    # --- crypto network chosen ---
    if data.startswith("cryptonet_"):
        network_key = data.split("_", 1)[1]
        network = CRYPTO_NETWORKS[network_key]

        state = user_state.get(user_id, {})
        state["method"] = f"crypto_{network_key}"
        state["stage"] = "awaiting_proof_button"
        user_state[user_id] = state

        details = crypto_details_text(network)
        keyboard = [
            [InlineKeyboardButton(
                "📋 Copy Wallet Address",
                copy_text=CopyTextButton(text=network["address"]),
            )],
            [InlineKeyboardButton("✅ I've Paid", callback_data="paid")],
        ]
        await query.message.reply_text(
            details, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )
        return

    # --- "I've Paid" pressed ---
    if data == "paid":
        state = user_state.get(user_id, {})
        state["stage"] = "awaiting_proof"
        user_state[user_id] = state
        await query.message.reply_text(
            "Great — please send a screenshot of the payment (or the transaction "
            "hash/reference as text) now, and I'll forward it for confirmation."
        )
        return

    # --- admin approves/rejects an order ---
    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_admin_decision(update, context, data)
        return


# ---------------------------------------------------------------------------
# Receiving proof (photo or text) from the buyer
# ---------------------------------------------------------------------------
async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_state.get(user_id)

    if not state or state.get("stage") != "awaiting_proof":
        return  # ignore random messages outside the payment flow

    product_key = state["product"]
    method_key = state["method"]
    product = PRODUCTS[product_key]
    order_id = f"{user_id}-{int(datetime.now().timestamp())}"

    order = {
        "order_id": order_id,
        "user_id": user_id,
        "username": update.effective_user.username or "(no username)",
        "first_name": update.effective_user.first_name or "",
        "product": product_key,
        "method": method_key,
        "amount": product["amount_for_record"],
        "status": "pending",
        "timestamp": datetime.now().isoformat(),
    }
    save_order(order)

    admin_keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order_id}"),
        ]
    ]
    caption = (
        f"🔔 *New order awaiting confirmation*\n\n"
        f"Order ID: `{order_id}`\n"
        f"User: @{order['username']} ({order['first_name']}, id `{user_id}`)\n"
        f"Product: {product['name']}\n"
        f"Amount: {product['amount_for_record']}\n"
        f"Method: {get_method_label(method_key)}"
    )

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=file_id,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        text_proof = update.message.text or "(no text provided)"
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"{caption}\n\nProof text: {text_proof}",
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )

    state["stage"] = "awaiting_admin"
    user_state[user_id] = state

    await update.message.reply_text(
        "Got it ✅ — I've sent your proof for confirmation. You'll get a message "
        "here as soon as it's approved (usually within a few hours)."
    )


# ---------------------------------------------------------------------------
# Admin taps Approve / Reject
# ---------------------------------------------------------------------------
async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query

    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("Only the admin can do this.", show_alert=True)
        return

    action, order_id = data.split("_", 1)
    orders = load_orders()
    order = next((o for o in orders if o["order_id"] == order_id), None)

    if not order:
        await query.answer("Order not found.", show_alert=True)
        return

    buyer_id = order["user_id"]
    product = PRODUCTS[order["product"]]

    if action == "approve":
        update_order_status(order_id, "approved")
        await context.bot.send_message(
            chat_id=buyer_id, text=product["delivery_text"], parse_mode=ParseMode.MARKDOWN
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ Approved and access sent for order {order_id}.")
    else:
        update_order_status(order_id, "rejected")
        await context.bot.send_message(
            chat_id=buyer_id,
            text=(
                "We couldn't confirm your payment ❌. Please double-check the "
                "amount/details and try again, or message here if you think "
                "this is a mistake."
            ),
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"❌ Rejected order {order_id}.")

    user_state.pop(buyer_id, None)


# ---------------------------------------------------------------------------
# /orders — quick admin command to see pending orders
# ---------------------------------------------------------------------------
async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    orders = [o for o in load_orders() if o["status"] == "pending"]
    if not orders:
        await update.message.reply_text("No pending orders.")
        return
    lines = [
        f"`{o['order_id']}` — @{o['username']} — {PRODUCTS[o['product']]['name']} — {o['amount']}"
        for o in orders
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


def main():
    if not BOT_TOKEN or ADMIN_CHAT_ID == 0:
        raise SystemExit(
            "Missing BOT_TOKEN and/or ADMIN_CHAT_ID.\n"
            "Set them as environment variables — locally, create a .env file "
            "(see .env.example); on Railway/Render, set them under your "
            "project's Variables tab."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT, proof_handler))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
