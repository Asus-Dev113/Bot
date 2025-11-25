# ---- BTC RPC integration (add to wallet.py) ----
from bitcoinrpc.authproxy import AuthServiceProxy
from decimal import Decimal
from urllib.parse import urlparse

def _btc_rpc_from_cfg():
    cfg = load_config()
    url  = cfg.get("btc_rpc_url", "http://127.0.0.1:8332").strip()
    user = cfg.get("btc_rpc_user", "").strip()
    pw   = cfg.get("btc_rpc_password", "").strip()
    if not user or not pw:
        raise RuntimeError("BTC RPC credentials missing (btc_rpc_user / btc_rpc_password) in config.json")
    u = urlparse(url)
    if u.username is None and u.password is None:
        url = f"{u.scheme}://{user}:{pw}@{u.hostname}:{u.port or 8332}"
    return AuthServiceProxy(url, timeout=60)

def is_valid_btc_address(addr: str) -> bool:
    if not isinstance(addr, str):
        return False
    addr = addr.strip()
    if addr.startswith("bc1"):
        return 14 <= len(addr) <= 90
    return addr[:1] in ("1","3") and (26 <= len(addr) <= 35)

def send_btc(user_id: int, to_address: str, amount_btc: float) -> str:
    if not is_valid_btc_address(to_address):
        raise ValueError("Invalid Bitcoin address")
    rpc = _btc_rpc_from_cfg()
    amt = Decimal(str(amount_btc))
    if amt <= 0:
        raise ValueError("Amount must be > 0")
    txid = rpc.sendtoaddress(to_address, float(amt))
    return str(txid)
