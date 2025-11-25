# exchange.py — FixedFloat integration with USD input + fee_pct from config
import time
import hmac
import hashlib
import json
from typing import Any, Dict, Optional

import requests

class FixedFloatError(Exception):
    pass

class FixedFloat:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: int = 25):
        if not base_url:
            raise FixedFloatError("FF base URL is empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").encode("utf-8")
        self.timeout = timeout

    def _headers(self, payload: Dict[str, Any]) -> Dict[str, str]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        sign = hmac.new(self.api_secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"X-API-KEY": self.api_key, "X-API-SIGN": sign, "Content-Type": "application/json"}

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = requests.post(url, headers=self._headers(payload), data=json.dumps(payload), timeout=self.timeout)
        if r.status_code >= 400:
            raise FixedFloatError(f"HTTP {r.status_code}: {r.text}")
        try:
            data = r.json()
        except Exception as e:
            raise FixedFloatError(f"Invalid JSON from FF: {e}")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise FixedFloatError(f"{err.get('code','ERR')}: {err.get('message','Unknown error')}")
        return data

    def create_order(
        self,
        *,
        from_currency: str,
        from_network: str,
        to_currency: str,
        to_network: str,
        amount: float,
        payout_address: str,
        rate: str = "fixed",
        amount_in: Optional[str] = None,   # e.g. "usd"
        refund_address: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "fromCurrency": from_currency.upper(),
            "fromNetwork": from_network.upper(),
            "toCurrency": to_currency.upper(),
            "toNetwork": "SPL" if to_network.upper() in ("SOL", "SPL") else to_network.upper(),
            "amount": float(amount),
            "type": rate.lower(),
            "payoutAddress": payout_address,
            "timestamp": int(time.time()),
        }
        if amount_in:
            payload["amountIn"] = amount_in  # "usd" → amount is treated as USD by FF
        if refund_address:
            payload["refundAddress"] = refund_address
        if extra:
            payload.update(extra)
        return self._post("/order/create", payload)

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._post("/order", {"id": str(order_id), "timestamp": int(time.time())})

# ---------- our app helpers ----------
from wallet import load_config, _db, add_usdt_internal

CFG = load_config()
FF_API_BASE = CFG.get("ff_api_base", "https://ff.io/api/v2").rstrip("/")
FF_API_KEY = CFG.get("ff_api_key", "")
FF_API_SECRET = CFG.get("ff_api_secret", "")
FEE_PCT = float(CFG.get("fee_pct", 0.01) or 0.01)   # decimal: 0.01 = 1%
DEFAULT_USDT_NET = (CFG.get("default_usdt_network", "TRC20") or "TRC20").upper()

_supported_from = {
    "BTC": ("BTC", "BTC"),
    "ETH": ("ETH", "ETH"),
    "TRX": ("TRX", "TRX"),
    "SOL": ("SOL", "SOL"),
}
_supported_usdt = {"TRC20": "TRC20", "ERC20": "ERC20", "SPL": "SPL", "SOL": "SPL"}

def _ensure_db():
    con = _db()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS exchanges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id TEXT UNIQUE,
            from_asset TEXT,
            amount_from REAL,
            amount_usd REAL,
            to_network TEXT,
            expected_usdt REAL,
            credited_usdt REAL DEFAULT 0,
            owner_fee REAL DEFAULT 0,
            status TEXT,
            created_at INTEGER,
            updated_at INTEGER
        )
    """
    )
    con.commit()
    con.close()

def _client() -> FixedFloat:
    if not FF_API_KEY or not FF_API_SECRET:
        raise FixedFloatError("Missing ff_api_key/ff_api_secret in config.json")
    return FixedFloat(FF_API_BASE, FF_API_KEY, FF_API_SECRET)

def get_owner_usdt_address(net: str, cfg: Dict[str, Any]) -> str:
    n = (net or "").lower().strip()
    if n == "trc20":
        return _owner_tron_address(cfg)
    elif n == "erc20":
        return _owner_evm_address(cfg)
    elif n in ("spl", "sol"):
        return _owner_solana_address(cfg)
    else:
        raise FixedFloatError(f"Unsupported USDT network '{net}'.")

def _owner_evm_address(cfg: Dict[str, Any]) -> str:
    pk = (cfg.get("evm_private_key", "") or "").strip()
    if not pk:
        raise FixedFloatError("evm_private_key is missing in config.json")
    from web3 import Web3
    acct = Web3().eth.account.from_key(pk)
    return Web3.to_checksum_address(acct.address)

def _owner_tron_address(cfg: Dict[str, Any]) -> str:
    pk = (cfg.get("tron_private_key", "") or "").strip()
    if not pk:
        raise FixedFloatError("tron_private_key is missing in config.json")
    import tronpy
    client = tronpy.Tron(network="mainnet")
    owner = client.generate_address(private_key=pk)
    return owner["base58check_address"]

def _owner_solana_address(cfg: Dict[str, Any]) -> str:
    sk = (cfg.get("solana_private_key", "") or "").strip()
    if not sk:
        raise FixedFloatError("solana_private_key is missing in config.json")
    from solana.keypair import Keypair
    from base58 import b58decode
    import json as _json
    if sk.startswith("["):
        secret = list(_json.loads(sk))
        kp = Keypair.from_secret_key(bytes(secret))
    else:
        kp = Keypair.from_secret_key(b58decode(sk))
    return str(kp.public_key)

def create_exchange_order(
    user_id: int,
    *,
    from_asset: str,
    amount_usd: float = None,     # user input in USD
    amount_from: float = None,    # fallback: coin units (not used now)
    usdt_network: str = None,
) -> dict:
    _ensure_db()
    a = (from_asset or "").upper()
    if a not in _supported_from:
        raise FixedFloatError(f"Unsupported from_asset: {a}")
    net = (usdt_network or DEFAULT_USDT_NET).upper()
    if net not in _supported_usdt:
        raise FixedFloatError(f"Unsupported USDT network: {net}")

    payout_address = get_owner_usdt_address(net, CFG)

    cli = _client()
    kwargs = dict(
        from_currency=a,
        from_network=_supported_from[a][1],
        to_currency="USDT",
        to_network=net,
        payout_address=payout_address,
        rate="fixed",
    )

    if amount_usd is not None:
        # FF: amountIn="usd" → FF מחשב את כמות המטבע הנדרשת
        resp = cli.create_order(amount=float(amount_usd), amount_in="usd", **kwargs)
    else:
        resp = cli.create_order(amount=float(amount_from or 0), **kwargs)

    data = resp.get("data") or resp
    order_id = str(data.get("id") or data.get("orderId") or "")
    if not order_id:
        raise FixedFloatError(f"FF create returned no order id: {data}")

    payin = data.get("payin") or {}
    deposit_address = payin.get("address") or payin.get("wallet") or ""
    deposit_amount = float(payin.get("amount") or 0)
    deposit_tag = payin.get("tag") or payin.get("memo")
    payout = data.get("payout") or {}
    expected_usdt = float(payout.get("amount") or 0.0)

    con = _db()
    cur = con.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO exchanges
           (user_id, order_id, from_asset, amount_from, amount_usd, to_network, expected_usdt, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?, 'created', strftime('%s','now'), strftime('%s','now'))""",
        (user_id, order_id, a, float(payin.get("amount") or 0.0), float(amount_usd or 0.0), net, expected_usdt),
    )
    con.commit()
    con.close()

    return {
        "order_id": order_id,
        "from_asset": a,
        "amount_from": float(payin.get("amount") or 0.0),
        "amount_usd": float(amount_usd or 0.0),
        "to_network": net,
        "expected_usdt": expected_usdt,
        "deposit_address": deposit_address,
        "deposit_amount": float(deposit_amount),
        "deposit_tag_memo": deposit_tag,
    }

def refresh_exchange_status(order_id: str) -> dict:
    _ensure_db()
    cli = _client()
    resp = cli.get_order(order_id)
    data = resp.get("data") or resp
    ff_status = str(data.get("status") or "").lower()

    con = _db()
    cur = con.cursor()
    cur.execute("SELECT user_id, status, expected_usdt, credited_usdt FROM exchanges WHERE order_id=?", (order_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        raise FixedFloatError("Order not found locally")
    user_id, old_status, expected_usdt, credited_usdt = row

    owner_fee = 0.0
    credited_now = 0.0
    if ff_status == "finished" and (credited_usdt or 0) <= 0:
        payout = data.get("payout") or {}
        gross = float(payout.get("amount") or expected_usdt or 0.0)
        owner_fee = round(gross * FEE_PCT, 2)
        user_net = round(gross - owner_fee, 2)
        if user_net < 0:
            user_net = 0.0
        add_usdt_internal(user_id, user_net)
        credited_now = user_net

    cur.execute(
        """UPDATE exchanges
           SET status=?,
               credited_usdt=COALESCE(credited_usdt,0)+?,
               owner_fee=COALESCE(owner_fee,0)+?,
               updated_at=strftime('%s','now')
           WHERE order_id=?""",
        (ff_status, credited_now, owner_fee, order_id),
    )
    con.commit()
    con.close()

    return get_exchange(order_id) | {"ff_status": ff_status}

def get_exchange(order_id: str):
    con = _db()
    cur = con.cursor()
    cur.execute(
        """SELECT user_id, order_id, from_asset, amount_from, amount_usd, to_network,
                  expected_usdt, credited_usdt, owner_fee, status, created_at, updated_at
           FROM exchanges WHERE order_id=?""",
        (order_id,),
    )
    r = cur.fetchone()
    con.close()
    if not r:
        return None
    (user_id, order_id, from_asset, amount_from, amount_usd, to_network,
     expected_usdt, credited_usdt, owner_fee, status, created_at, updated_at) = r
    return {
        "user_id": user_id,
        "order_id": order_id,
        "from_asset": from_asset,
        "amount_from": amount_from,
        "amount_usd": amount_usd,
        "to_network": to_network,
        "expected_usdt": expected_usdt,
        "credited_usdt": credited_usdt,
        "owner_fee": owner_fee,
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
    }

def render_create_result_text(info: dict) -> str:
    lines = [
        "✅ Exchange order created.",
        f"• Order ID: {info['order_id']}",
        f"• From: {info['from_asset']}  (≈ ${info.get('amount_usd', 0):.2f})",
        f"• To: USDT ({info['to_network']})",
        f"• Expected USDT (gross): {info['expected_usdt']}",
        "",
        "➡️ Send deposit:",
        f"• Address: {info['deposit_address']}",
        f"• Amount to send: {info['deposit_amount']} {info['from_asset']}",
    ]
    if info.get("deposit_tag_memo"):
        lines.append(f"• Memo / Tag: {info['deposit_tag_memo']}")
    lines += [
        "",
        "When finished, your internal USDT balance will be credited (minus fee).",
        "Check status: /status <order_id>",
    ]
    return "\n".join(lines)

def render_status_text(row: dict) -> str:
    lines = [
        "ℹ️ Exchange status",
        f"• Order ID: {row['order_id']}",
        f"• Status: {row.get('status') or row.get('ff_status')}",
        f"• Network: USDT ({row['to_network']})",
        f"• Expected USDT: {row.get('expected_usdt', 0)}",
        f"• Credited to internal balance: {row.get('credited_usdt', 0)}",
    ]
    if float(row.get("owner_fee") or 0) > 0:
        lines.append(f"• Owner fee: {row['owner_fee']}")
    return "\n".join(lines)
