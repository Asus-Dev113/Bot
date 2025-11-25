
import json
import time
from dataclasses import dataclass

from tronpy import Tron
from tronpy.keys import PrivateKey

@dataclass
class TronSwapConfig:
    node_url: str
    router: str

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def _client(node_url: str) -> Tron:
    return Tron(provider=node_url)

def _wtrx() -> str:
    # placeholder WTRX for path (SunSwap handles native TRX with call_value)
    return "TRWryeK3x2fJGZveHFkNjEcVuJ39nYjJPT"

def _usdt(cfg: dict) -> str:
    return cfg.get("usdt_trc20", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")

def swap_trx_to_usdt(cfg: TronSwapConfig, priv_hex: str, amount_trx: float) -> str:
    c = _client(cfg.node_url)
    pk = PrivateKey(bytes.fromhex(priv_hex))
    conf = load_config()
    usdt = _usdt(conf)
    router = c.get_contract(cfg.router)

    amount_out_min = 0
    path = [c.get_w3().to_base58check_address(_wtrx()), c.get_w3().to_base58check_address(usdt)]
    deadline = int(time.time()) + 600

    tx = (router.functions.swapExactTRXForTokens(amount_out_min, path, pk.public_key.to_base58check_address(), deadline)
          .with_owner(pk.public_key.to_base58check_address())
          .fee_limit(15_000_000)
          .call_value(int(amount_trx * 1_000_000))
          .build()
          .sign(pk))
    ret = tx.broadcast().wait()
    return ret["txid"]

def swap_usdt_to_trx(cfg: TronSwapConfig, priv_hex: str, amount_usdt: float) -> str:
    c = _client(cfg.node_url)
    pk = PrivateKey(bytes.fromhex(priv_hex))
    conf = load_config()
    usdt = conf.get("usdt_trc20", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    router = c.get_contract(cfg.router)

    # Approve USDT
    erc20 = c.get_contract(usdt)
    amount_units = int(amount_usdt * 1_000_000)
    txa = (erc20.functions.approve(cfg.router, amount_units)
           .with_owner(pk.public_key.to_base58check_address())
           .fee_limit(10_000_000)
           .build()
           .sign(pk))
    txa.broadcast().wait()

    amount_out_min = 0
    path = [c.get_w3().to_base58check_address(usdt), c.get_w3().to_base58check_address(_wtrx())]
    deadline = int(time.time()) + 600

    tx = (router.functions.swapExactTokensForTRX(amount_units, amount_out_min, path, pk.public_key.to_base58check_address(), deadline)
          .with_owner(pk.public_key.to_base58check_address())
          .fee_limit(20_000_000)
          .build()
          .sign(pk))
    ret = tx.broadcast().wait()
    return ret["txid"]
