# BTC RPC setup (Bitcoin Core) + bot integration
1) Install Bitcoin Core, edit %APPDATA%\Bitcoin\bitcoin.conf:
   server=1
   rpcuser=yourStrongRpcUser
   rpcpassword=yourVeryStrongRpcPassword123!@#
   rpcallowip=127.0.0.1
   rpcport=8332
2) Restart Core and sync.
3) In config.json add keys:
   btc_rpc_url, btc_rpc_user, btc_rpc_password
4) pip install python-bitcoinrpc
5) Run the bot and test Withdraw → BTC.
