# bot.py — aiogram v3
# Exchange flow asks for USD amount (not coin units) for BTC/ETH/TRX/SOL → USDT.
import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# ---------- config ----------
CFG_PATH = Path("config.json")
if not CFG_PATH.exists():
    raise FileNotFoundError(f"config.json not found at {CFG_PATH.resolve()}")

with CFG_PATH.open("r", encoding="utf-8") as f:
    CFG = json.load(f)

BOT_TOKEN = CFG["bot_token"]

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

# ---------- bot / dp ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ---------- wallet helpers ----------
from wallet import build_wallet_text_plain, get_or_create_user_wallets

# ---------- exchange helpers ----------
from exchange import (
    create_exchange_order,
    refresh_exchange_status,
    render_create_result_text,
    render_status_text,
)

# ---------- keyboards ----------
def main_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="💼 Wallet", callback_data="wallet")],
            [types.InlineKeyboardButton(text="💱 Exchange", callback_data="exchange")],
            [types.InlineKeyboardButton(text="📤 Withdraw", callback_data="withdraw")],
        ]
    )

def back_menu() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")]]
    )

def exchange_asset_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="BTC", callback_data="ex_from_BTC"),
             types.InlineKeyboardButton(text="ETH", callback_data="ex_from_ETH")],
            [types.InlineKeyboardButton(text="TRX", callback_data="ex_from_TRX"),
             types.InlineKeyboardButton(text="SOL", callback_data="ex_from_SOL")],
            [types.InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
        ]
    )

def usdt_network_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="USDT (TRC20)", callback_data="usdt_TRON")],
            [types.InlineKeyboardButton(text="USDT (ERC20)", callback_data="usdt_ETH")],
            [types.InlineKeyboardButton(text="USDT (SOL)",   callback_data="usdt_SOL")],
            [types.InlineKeyboardButton(text="⬅️ Back", callback_data="cancel_exchange")],
        ]
    )

# ---------- FSM ----------
class ExStates(StatesGroup):
    from_asset = State()
    amount_usd = State()
    usdt_net = State()

# ---------- handlers ----------
@router.message(F.text == "/start")
async def start_cmd(m: types.Message):
    get_or_create_user_wallets(m.from_user.id)
    await m.answer("Main Menu:", reply_markup=main_menu_kb())

@router.callback_query(F.data == "back_main")
async def back_main(cb: types.CallbackQuery):
    await cb.message.edit_text("Main Menu:", reply_markup=main_menu_kb())
    await cb.answer()

@router.callback_query(F.data == "wallet")
async def wallet_cb(cb: types.CallbackQuery):
    try:
        text = build_wallet_text_plain(cb.from_user.id)
    except Exception as e:
        logging.exception("Wallet view failed")
        text = f"Wallet error: {e}"
    await cb.message.answer(text, reply_markup=back_menu())
    await cb.answer()

@router.message(F.text == "/wallet")
async def wallet_cmd(m: types.Message):
    try:
        text = build_wallet_text_plain(m.from_user.id)
    except Exception as e:
        logging.exception("Wallet cmd failed")
        text = f"Wallet error: {e}"
    await m.answer(text)

@router.callback_query(F.data == "exchange")
async def exchange_cb(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ExStates.from_asset)
    await cb.message.answer("Choose the coin you want to exchange to USDT:", reply_markup=exchange_asset_kb())
    await cb.answer()

@router.callback_query(F.data == "cancel_exchange")
async def cancel_exchange(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Main Menu:", reply_markup=main_menu_kb())
    await cb.answer()

@router.callback_query(F.data.startswith("ex_from_"))
async def choose_from_asset(cb: types.CallbackQuery, state: FSMContext):
    asset = cb.data.replace("ex_from_", "")
    await state.update_data(from_asset=asset)
    await state.set_state(ExStates.amount_usd)

    msg = (
        f"Enter the amount in *USD* you want to exchange from {asset} to USDT.\n\n"
        f"• Use digits only (decimals allowed).\n"
        f"• Examples: `25` → $25,  `100.5` → $100.50\n"
        f"• We’ll convert the USD value to the required {asset} amount automatically."
    )
    await cb.message.answer(msg, reply_markup=back_menu())
    await cb.answer()

@router.message(ExStates.amount_usd)
async def set_amount_usd(m: types.Message, state: FSMContext):
    raw = (m.text or "").strip().replace(",", ".")
    try:
        amount_usd = float(raw)
        if amount_usd <= 0:
            raise ValueError
    except Exception:
        await m.answer("Invalid amount. Please send a positive USD value, e.g. 25 or 100.5")
        return

    await state.update_data(amount_usd=amount_usd)
    await state.set_state(ExStates.usdt_net)
    await m.answer("Choose USDT network:", reply_markup=usdt_network_kb())

@router.callback_query(ExStates.usdt_net, F.data.startswith("usdt_"))
async def choose_usdt_net(cb: types.CallbackQuery, state: FSMContext):
    mapping = {"usdt_TRON": "TRC20", "usdt_ETH": "ERC20", "usdt_SOL": "SPL"}
    net = mapping.get(cb.data, "TRC20")
    data = await state.get_data()
    from_asset = data["from_asset"]
    amount_usd = float(data["amount_usd"])

    try:
        info = create_exchange_order(
            cb.from_user.id,
            from_asset=from_asset,
            amount_usd=amount_usd,     # <— סכום בדולרים
            usdt_network=net
        )
        text = render_create_result_text(info)
    except Exception as e:
        logging.exception("Create exchange failed")
        text = f"Exchange error: {e}"

    await state.clear()
    await cb.message.answer(text, reply_markup=back_menu())
    await cb.answer()

@router.message(F.text.startswith("/status"))
async def status_cmd(m: types.Message):
    parts = m.text.strip().split()
    if len(parts) < 2:
        await m.answer("Usage: /status <order_id>")
        return
    order_id = parts[1]
    try:
        row = refresh_exchange_status(order_id)
        await m.answer(render_status_text(row))
    except Exception as e:
        await m.answer(f"Status error: {e}")

@router.callback_query(F.data == "withdraw")
async def withdraw_cb(cb: types.CallbackQuery):
    await cb.message.answer("Withdraw menu.", reply_markup=back_menu())
    await cb.answer()

# ---------- runner ----------
async def main():
    logging.info("Starting polling…")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
