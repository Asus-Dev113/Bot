
import os
import json
from dataclasses import dataclass
from typing import Optional

import requests
from web3 import Web3
from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware as geth_poa_middleware
from eth_account import Account

@dataclass
class EthSwapConfig:
    evm_rpc: str
    usdt_erc20: str
    uni_router: str
    uni_quoter: str

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def _w3(rpc: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    try:
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    except Exception:
        pass
    return w3

def _private_key_from_env_or_hex(hex_key: str) -> str:
    if hex_key and len(hex_key) >= 32:
        return hex_key
    env = os.getenv("BOT_ETH_PRIV", "")
    if not env:
        raise RuntimeError("ETH private key missing. Provide hex or BOT_ETH_PRIV env.")
    return env

def _quote_0x(from_token: str, to_token: str, amount: int) -> Optional[dict]:
    try:
        url = f"https://api.0x.org/swap/v1/quote?sellToken={from_token}&buyToken={to_token}&sellAmount={amount}"
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def swap_eth_to_usdt(cfg: EthSwapConfig, priv_hex: str, amount_eth: float) -> str:
    w3 = _w3(cfg.evm_rpc)
    priv = _private_key_from_env_or_hex(priv_hex)
    acct = Account.from_key(priv)
    amount_wei = int(amount_eth * 10**18)

    q = _quote_0x("ETH", cfg.usdt_erc20, amount_wei)
    if not q:
        raise RuntimeError("0x quote failed for ETH->USDT")
    tx = {
        "to": Web3.to_checksum_address(q["to"]),
        "data": q["data"],
        "value": int(q.get("value", "0")),
        "gas": int(q.get("gas", 700000)),
        "gasPrice": int(q.get("gasPrice", w3.eth.gas_price)),
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key=priv)
    txh = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
    return txh

def swap_usdt_to_eth(cfg: EthSwapConfig, priv_hex: str, amount_usdt: float) -> str:
    w3 = _w3(cfg.evm_rpc)
    priv = _private_key_from_env_or_hex(priv_hex)
    acct = Account.from_key(priv)
    amount_units = int(amount_usdt * 1_000_000)

    erc20_abi = [{"constant":False,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]
    usdt = w3.eth.contract(address=Web3.to_checksum_address(cfg.usdt_erc20), abi=erc20_abi)
    nonce = w3.eth.get_transaction_count(acct.address)
    txa = usdt.functions.approve(Web3.to_checksum_address("0xDef1C0ded9bec7F1a1670819833240f027b25EfF"), amount_units).build_transaction({
        "from": acct.address, "nonce": nonce, "gas": 90000, "gasPrice": w3.eth.gas_price
    })
    signed = w3.eth.account.sign_transaction(txa, private_key=priv)
    w3.eth.send_raw_transaction(signed.rawTransaction)

    q = _quote_0x(cfg.usdt_erc20, "ETH", amount_units)
    if not q:
        raise RuntimeError("0x quote failed for USDT->ETH")
    tx = {
        "to": Web3.to_checksum_address(q["to"]),
        "data": q["data"],
        "value": int(q.get("value", "0")),
        "gas": int(q.get("gas", 700000)),
        "gasPrice": int(q.get("gasPrice", w3.eth.gas_price)),
        "nonce": nonce + 1,
        "chainId": w3.eth.chain_id,
    }
    signed2 = w3.eth.account.sign_transaction(tx, private_key=priv)
    txh = w3.eth.send_raw_transaction(signed2.rawTransaction).hex()
    return txh
