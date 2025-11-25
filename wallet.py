# wallet.py — plain text addresses + live balances (no links)
from __future__ import annotations
import os, re, json, sqlite3, requests
from typing import Tuple, Optional


# ---------------- Config ----------------
def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()
DB_PATH: str = CFG.get("db_path", "users.db")
SOLANA_RPC: str = CFG.get("solana_rpc", "https://api.mainnet-beta.solana.com")
BSC_RPC: str = CFG.get("bsc_rpc", "{BSC_RPC}")
AVAX_RPC: str = CFG.get("avax_rpc", "{AVAX_RPC}")
RIPPLE_API: str = CFG.get("ripple_api", "{RIPPLE_API}")
STELLAR_HORIZON: str = CFG.get("stellar_horizon", "{STELLAR_HORIZON}")


# ---------------- DB (safe migration) ----------------
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_hd (
        user_id INTEGER PRIMARY KEY,
        idx INTEGER UNIQUE
    )""")
    need = {
        "sol_addr": "TEXT",
        "eth_addr": "TEXT",
        "trx_addr": "TEXT",
        "btc_addr": "TEXT",
        "usdt_internal": "REAL DEFAULT 0",
        "xrp_addr": "TEXT",
        "ltc_addr": "TEXT",
        "xlm_addr": "TEXT",
        "bnb_addr": "TEXT",
        "avax_addr": "TEXT",
        "doge_addr": "TEXT",
        "ada_addr": "TEXT"
    }
    cur.execute("PRAGMA table_info(users)")
    cols = {r[1] for r in cur.fetchall()}
    for col, decl in need.items():
        if col not in cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
    con.commit()
    return con


def _get_or_create_user_index(user_id: int) -> int:
    con = _db();
    cur = con.cursor()
    cur.execute("SELECT idx FROM user_hd WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if r:
        con.close();
        return int(r[0])
    cur.execute("SELECT MAX(idx) FROM user_hd");
    mx = cur.fetchone()[0]
    idx = (mx + 1) if mx is not None else 0
    cur.execute("INSERT INTO user_hd (user_id, idx) VALUES (?,?)", (user_id, idx))
    con.commit();
    con.close()
    return idx


def _get_user_row(user_id: int) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], float, dict]:
    con = _db();
    cur = con.cursor()
    cur.execute(
        "SELECT sol_addr, eth_addr, trx_addr, btc_addr, COALESCE(usdt_internal,0), xrp_addr, ltc_addr, xlm_addr, bnb_addr, avax_addr, doge_addr, ada_addr FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cur.fetchone();
    con.close()
    if not row: return (None, None, None, None, 0.0, {})
    sol, eth, trx, btc, usdt_bal, xrp, ltc, xlm, bnb, avax, doge, ada = row
    extras = {"XRP": xrp, "LTC": ltc, "XLM": xlm, "BNB": bnb, "AVAX": avax, "DOGE": doge, "ADA": ada}
    return sol, eth, trx, btc, float(usdt_bal or 0.0), extras


def set_user_address(user_id: int, chain: str, address: str) -> None:
    chain = chain.lower()
    if chain not in ("sol", "eth", "trx", "btc", "xrp", "ltc", "xlm", "bnb", "avax", "doge", "ada"):
        raise ValueError("Unsupported chain for set_user_address")
    con = _db()
    con.execute(f"UPDATE users SET {chain}_addr=? WHERE user_id=?", (address, user_id))
    if con.total_changes == 0:
        con.execute(f"INSERT INTO users (user_id, {chain}_addr) VALUES (?, ?)", (user_id, address))
    con.commit();
    con.close()


def add_usdt_internal(user_id: int, delta: float) -> None:
    con = _db();
    cur = con.cursor()
    cur.execute("UPDATE users SET usdt_internal=COALESCE(usdt_internal,0)+? WHERE user_id=?", (float(delta), user_id))
    if cur.rowcount == 0:
        cur.execute("INSERT OR IGNORE INTO users (user_id, usdt_internal) VALUES (?,?)", (user_id, float(delta)))
    con.commit();
    con.close()


# ---------------- HD derivation ----------------
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from mnemonic import Mnemonic


def _get_hd_mnemonic() -> str:
    m = os.getenv("BOT_HD_MNEMONIC", "").strip() or CFG.get("hd_mnemonic", "").strip()
    if not m:
        raise RuntimeError("HD mnemonic is not set. Add BOT_HD_MNEMONIC or config['hd_mnemonic'].")
    m = re.sub(r"\s+", " ", m.replace(",", " ").strip().lower())
    m = re.sub(r"^\s*english:\s*", "", m)
    if not re.fullmatch(r"[a-z ]+", m): raise RuntimeError("HD mnemonic must be English words only.")
    words = m.split(" ");
    assert len(words) in (12, 24), "HD mnemonic must be 12 or 24 words."
    if not Mnemonic("english").check(m): raise RuntimeError("HD mnemonic failed validation.")
    return m


def _derive_addresses_for_index(idx: int):
    seed = Bip39SeedGenerator(_get_hd_mnemonic()).Generate()

    # Derive addresses for main cryptocurrencies
    eth_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)
    trx_ctx = Bip44.FromSeed(seed, Bip44Coins.TRON).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)
    btc_ctx = Bip44.FromSeed(seed, Bip44Coins.BITCOIN).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)
    sol_ctx = Bip44.FromSeed(seed, Bip44Coins.SOLANA).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)

    # Derive addresses for additional cryptocurrencies
    xrp_ctx = Bip44.FromSeed(seed, Bip44Coins.RIPPLE).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)
    ltc_ctx = Bip44.FromSeed(seed, Bip44Coins.LITECOIN).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)

    # For BNB, use the same derivation as ETH since they're both EVM chains
    bnb_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)

    # For AVAX, use the same derivation as ETH since it's also an EVM chain
    avax_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)

    doge_ctx = Bip44.FromSeed(seed, Bip44Coins.DOGECOIN).Purpose().Coin().Account(0).Change(
        Bip44Changes.CHAIN_EXT).AddressIndex(idx)

    # For Stellar (XLM) and Cardano (ADA), we'll generate placeholder addresses since they use different derivation methods
    # In a real implementation, you would use the appropriate libraries for these blockchains
    xlm_addr = f"stellar_placeholder_{idx}"  # Replace with actual Stellar address generation
    ada_addr = f"cardano_placeholder_{idx}"  # Replace with actual Cardano address generation

    return {
        "sol": sol_ctx.PublicKey().ToAddress(),
        "eth": eth_ctx.PublicKey().ToAddress(),
        "trx": trx_ctx.PublicKey().ToAddress(),
        "btc": btc_ctx.PublicKey().ToAddress(),
        "xrp": xrp_ctx.PublicKey().ToAddress(),
        "ltc": ltc_ctx.PublicKey().ToAddress(),
        "bnb": bnb_ctx.PublicKey().ToAddress(),
        "avax": avax_ctx.PublicKey().ToAddress(),
        "doge": doge_ctx.PublicKey().ToAddress(),
        "xlm": xlm_addr,
        "ada": ada_addr
    }


def get_or_create_user_wallets(user_id: int) -> None:
    con = _db();
    cur = con.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (user_id, usdt_internal) VALUES (?, 0)", (user_id,))
        con.commit()
    con.close()

    idx = _get_or_create_user_index(user_id)
    addresses = _derive_addresses_for_index(idx)

    con = _db();
    cur = con.cursor()
    cur.execute(
        "SELECT sol_addr, eth_addr, trx_addr, btc_addr, xrp_addr, ltc_addr, xlm_addr, bnb_addr, avax_addr, doge_addr, ada_addr FROM users WHERE user_id=?",
        (user_id,)
    )
    row = cur.fetchone() or (None,) * 11
    current = {
        "sol": row[0], "eth": row[1], "trx": row[2], "btc": row[3],
        "xrp": row[4], "ltc": row[5], "xlm": row[6], "bnb": row[7],
        "avax": row[8], "doge": row[9], "ada": row[10]
    }

    for k, v in addresses.items():
        if not current[k]:
            cur.execute(f"UPDATE users SET {k}_addr=? WHERE user_id=?", (v, user_id))
    con.commit();
    con.close()


# ---------------- Balances (no links) ----------------
# --- Optional balances for extra assets (stubs default to 0.0; replace with real API if needed) ---
def get_xrp_balance(addr: str) -> float:
    """XRP native balance using Ripple Data API (no key)."""
    try:
        if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
            return 0.0
        r = requests.get(f"{RIPPLE_API}/v2/accounts/{addr}/balances?currency=XRP", timeout=10)
        j = r.json()
        if isinstance(j, dict) and "balances" in j and j["balances"]:
            val = j["balances"][0].get("value", "0")
            return float(val)
    except Exception:
        pass
    return 0.0


def get_ltc_balance(addr: str) -> float:
    try:
        if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
            return 0.0
        r = requests.get(f"https://api.blockcypher.com/v1/ltc/main/addrs/{addr}/balance", timeout=10)
        sat = r.json().get("final_balance", 0) or 0
        return sat / 1e8
    except Exception:
        return 0.0


def get_xlm_balance(addr: str) -> float:
    """XLM native balance using Stellar Horizon (no key)."""
    try:
        if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
            return 0.0
        r = requests.get(f"{STELLAR_HORIZON}/accounts/{addr}", timeout=10)
        j = r.json()
        if isinstance(j, dict) and "balances" in j:
            for b in j["balances"]:
                if b.get("asset_type") == "native":
                    return float(b.get("balance", "0"))
    except Exception:
        pass
    return 0.0


def _eth_get_balance_jsonrpc(endpoint: str, address: str) -> float:
    """Generic JSON-RPC eth_getBalance -> float in native coin units."""
    try:
        import json
        payload = {"jsonrpc": "2.0", "method": "eth_getBalance", "params": [address, "latest"], "id": 1}
        r = requests.post(endpoint, json=payload, timeout=10)
        j = r.json()
        if "result" in j:
            wei = int(j["result"], 16)
            return wei / 1e18
    except Exception:
        pass
    return 0.0


def get_bnb_balance(addr: str) -> float:
    """BNB balance on BSC (EVM) via public endpoint."""
    if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
        return 0.0
    if not addr.startswith("0x"): return 0.0
    return _eth_get_balance_jsonrpc("{BSC_RPC}", addr)


def get_avax_balance(addr: str) -> float:
    """AVAX C-Chain (EVM) via public endpoint."""
    if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
        return 0.0
    if not addr.startswith("0x"): return 0.0
    return _eth_get_balance_jsonrpc("{AVAX_RPC}", addr)


def get_doge_balance(addr: str) -> float:
    try:
        if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
            return 0.0
        r = requests.get(f"https://api.blockcypher.com/v1/doge/main/addrs/{addr}/balance", timeout=10)
        sat = r.json().get("final_balance", 0) or 0
        return sat / 1e8
    except Exception:
        return 0.0


def get_ada_balance(addr: str) -> float:
    """Cardano ADA — requires indexer; leaving stub 0.0 until API key is configured."""
    if not addr or addr.startswith("stellar_placeholder_") or addr.startswith("cardano_placeholder_"):
        return 0.0
    return 0.0


def get_sol_balance(addr: str) -> float:
    if not addr: return 0.0
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [addr]}
        r = requests.post(SOLANA_RPC, json=payload, timeout=10)
        lamports = (r.json().get("result") or {}).get("value", 0)
        return (lamports or 0) / 1_000_000_000
    except Exception:
        return 0.0


def get_btc_balance(addr: str) -> float:
    if not addr: return 0.0
    try:
        r = requests.get(f"https://api.blockcypher.com/v1/btc/main/addrs/{addr}/balance", timeout=10)
        sat = r.json().get("final_balance", 0) or 0
        return sat / 1e8
    except Exception:
        return 0.0


def get_eth_balance(addr: str) -> float:
    if not addr: return 0.0
    try:
        r = requests.get(f"https://api.blockcypher.com/v1/eth/main/addrs/{addr}/balance", timeout=10)
        wei = r.json().get("final_balance", 0) or 0
        return wei / 1e18
    except Exception:
        return 0.0


def get_trx_balance(addr: str) -> float:
    if not addr: return 0.0
    try:
        r = requests.get(f"https://apilist.tronscanapi.com/api/account?address={addr}", timeout=10)
        j = r.json()
        sun = j.get("balance", 0)
        if not sun and "data" in j and j["data"]:
            sun = j["data"][0].get("balance", 0)
        return (sun or 0) / 1_000_000
    except Exception:
        return 0.0


# ---------------- Plain text wallet view ----------------
def build_wallet_text_plain(user_id: int) -> str:
    get_or_create_user_wallets(user_id)
    sol, eth, trx, btc, usdt_bal, extra = _get_user_row(user_id)

    sol_bal = get_sol_balance(sol)
    btc_bal = get_btc_balance(btc)
    eth_bal = get_eth_balance(eth)
    trx_bal = get_trx_balance(trx)

    lines = [
        "💼 Your Wallet",
        "",
        "🔹 SOL",
        f"• Address: {sol or 'not set'}",
        f"• Balance: {sol_bal:.6f} SOL",
        "",
        "🔹 BTC",
        f"• Address: {btc or 'not set'}",
        f"• Balance: {btc_bal:.8f} BTC",
        "",
        "🔹 ETH",
        f"• Address: {eth or 'not set'}",
        f"• Balance: {eth_bal:.6f} ETH",
        "",
        "🔹 TRX",
        f"• Address: {trx or 'not set'}",
        f"• Balance: {trx_bal:.6f} TRX",
        "",
        "🔹 XRP",
        f"• Address: {extra.get('XRP') or 'not set'}",
        f"• Balance: {get_xrp_balance(extra.get('XRP')):.6f} XRP",
        "",
        "🔹 LTC",
        f"• Address: {extra.get('LTC') or 'not set'}",
        f"• Balance: {get_ltc_balance(extra.get('LTC')):.8f} LTC",
        "",
        "🔹 XLM",
        f"• Address: {extra.get('XLM') or 'not set'}",
        f"• Balance: {get_xlm_balance(extra.get('XLM')):.6f} XLM",
        "",
        "🔹 BNB",
        f"• Address: {extra.get('BNB') or 'not set'}",
        f"• Balance: {get_bnb_balance(extra.get('BNB')):.6f} BNB",
        "",
        "🔹 AVAX",
        f"• Address: {extra.get('AVAX') or 'not set'}",
        f"• Balance: {get_avax_balance(extra.get('AVAX')):.6f} AVAX",
        "",
        "🔹 DOGE",
        f"• Address: {extra.get('DOGE') or 'not set'}",
        f"• Balance: {get_doge_balance(extra.get('DOGE')):.8f} DOGE",
        "",
        "🔹 ADA",
        f"• Address: {extra.get('ADA') or 'not set'}",
        f"• Balance: {get_ada_balance(extra.get('ADA')):.6f} ADA",
        "",
        "🔹 USDT (internal)",
        f"• Balance: {usdt_bal:.2f} USDT",
    ]
    return "\n".join(lines)


# Backward compatibility name if your bot still imports build_wallet_text
def build_wallet_text(user_id: int) -> str:
    return build_wallet_text_plain(user_id)