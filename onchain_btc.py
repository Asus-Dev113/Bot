
import json
import time
import hmac
import hashlib
import requests
from dataclasses import dataclass

@dataclass
class BtcSwapConfig:
    api_base: str
    api_key: str
    api_secret: str
    settle_usdt_chain: str = "trc20"

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def create_order_btc_to_usdt(cfg: BtcSwapConfig, refund_btc: str, usdt_dest: str, amount_btc: float):
    if not cfg.api_base or not cfg.api_key or not cfg.api_secret:
        raise RuntimeError("FixedFloat API keys missing in config.json")
    ts = int(time.time() * 1000)
    body = {
        "from": "BTC",
        "to": "USDT",
        "network": cfg.settle_usdt_chain.upper(),
        "amount": amount_btc,
        "refundAddress": refund_btc,
        "payoutAddress": usdt_dest
    }
    payload = json.dumps(body, separators=(",", ":"))
    sig = _sign(cfg.api_secret, f"{ts}{payload}")
    r = requests.post(f"{cfg.api_base}/order/create",
                      headers={"X-API-KEY": cfg.api_key, "X-API-TS": str(ts), "X-API-SIGN": sig,
                               "Content-Type": "application/json"},
                      data=payload, timeout=20)
    r.raise_for_status()
    return r.json()
