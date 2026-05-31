# Cloud Deployment

This bot can run on a small Ubuntu VM in NAVER Cloud Platform or Google Cloud.
Use the GUI app on your PC for manual monitoring, and run `server_bot.py` on the
cloud server for 24/7 trading.

## Before You Start

1. Create an Ubuntu server.
2. Assign a public static IP address.
3. Allow SSH only from your own IP address.
4. Register the server public IP in the Upbit Open API allowlist.
5. Use an Upbit API key with order permission only. Do not enable withdrawal.

## Provider Notes

### NAVER Cloud

1. Create an Ubuntu server from `Services > Compute > Server`.
2. Apply or create an ACG.
3. Add an inbound ACG rule for SSH:
   - Protocol: TCP
   - Source: your current public IP, for example `1.2.3.4/32`
   - Port: `22`
4. Request and assign a Public IP.
5. Add that Public IP to the Upbit API allowlist.

### Google Cloud

1. Create a Compute Engine VM.
2. For the lowest-cost start, use `e2-micro`. Google Free Tier applies only in
   selected US regions such as `us-west1`, `us-central1`, and `us-east1`.
3. Reserve and assign a static external IP address.
4. Allow SSH only from your current public IP in the firewall rule.
5. Add the static external IP to the Upbit API allowlist.

## Install

SSH into the server and run:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/preciselee84-oss/bitcoin_trader.git
cd bitcoin_trader
bash scripts/install_cloud.sh
```

Edit `.env`:

```bash
nano .env
```

At minimum, set:

```env
UPBIT_ACCESS_KEY=your_access_key_here
UPBIT_SECRET_KEY=your_secret_key_here
TICKER=KRW-BTC
DRY_RUN=true
```

Keep `DRY_RUN=true` for the first test. It logs signals but skips real orders.

## Test

```bash
source .venv/bin/activate
python server_bot.py
```

Stop the test with `Ctrl+C`. If logs look normal, change `.env`:

```env
DRY_RUN=false
```

## Run As A Service

```bash
sudo systemctl start bitcoin-trader
sudo systemctl status bitcoin-trader
journalctl -u bitcoin-trader -f
```

The service starts automatically after server reboot because the installer runs:

```bash
sudo systemctl enable bitcoin-trader
```

## Update

```bash
cd ~/bitcoin_trader
git pull
bash scripts/install_cloud.sh
sudo systemctl restart bitcoin-trader
journalctl -u bitcoin-trader -f
```

## Useful Commands

```bash
sudo systemctl stop bitcoin-trader
sudo systemctl restart bitcoin-trader
sudo systemctl status bitcoin-trader
journalctl -u bitcoin-trader -n 100 --no-pager
tail -f trading.log
```

## Safety Checklist

- Keep `.env` private. The installer sets it to `chmod 600`.
- Never commit `.env`.
- Do not enable Upbit withdrawal permission.
- Use a static public IP and register it in Upbit.
- Start with `DRY_RUN=true`.
- Run `python backtest.py` before enabling live trading.
