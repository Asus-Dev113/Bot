
import base64
import json
import requests
from dataclasses import dataclass
from solders.transaction import VersionedTransaction

@dataclass
class JupiterCfg:
    base: str = "https://quote-api.jup.ag/v6"

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

LAMPORTS_PER_SOL = 1_000_000_000

def perform_swap_solana(sol_client, keypair, input_mint: str, output_mint: str, amount_smallest: int, slippage_bps: int = 50) -> str:
    cfg = load_config()
    base = cfg.get("jupiter_base", "https://quote-api.jup.ag/v6")

    quote = requests.get(f"{base}/quote", params={
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount_smallest,
        "slippageBps": slippage_bps
    }, timeout=20).json()

    routes = quote.get("data") or []
    if not routes:
        raise RuntimeError("Jupiter quote failed")

    route = routes[0]
    swap_req = {
        "quoteResponse": route,
        "userPublicKey": str(keypair.pubkey())
    }
    swap = requests.post(f"{base}/swap", json=swap_req, timeout=20).json()
    if "swapTransaction" not in swap:
        raise RuntimeError("Jupiter swap build failed")

    raw = base64.b64decode(swap["swapTransaction"])
    tx = VersionedTransaction.from_bytes(raw)
    tx.sign([keypair])
    resp = sol_client.send_raw_transaction(tx.to_bytes())
    try:
        sig = getattr(resp, "value", None) or resp
    except Exception:
        sig = resp
    return str(sig)
