# Grepolis GPT-Bot-BR Runner

Automates Grepolis login + GPT-Bot-BR panel authentication via proxy. Designed to run multiple bot instances on a VPS with low resource usage.

## Requirements

- Python 3.11+
- Linux VPS (Ubuntu 22.04 / 24.04 recommended)

## VPS Installation (Ubuntu)

```bash
# 1. Install system dependencies
apt update && apt install -y python3 python3-pip python3-venv \
    libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxshmfence1

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install Chromium for Playwright
playwright install chromium
playwright install-deps chromium
```

## Configuration

Copy `accounts.example.json` to `accounts.json` and fill in your data:

```json
{
  "bot_login": "login_bot",
  "bot_password": "password_bot",
  "world": "NAME_WORLD",
  "accounts": [
    {
      "grepolis_login": "account_name",
      "grepolis_password": "password",
      "grepolis_url": "https://xxxx.grepolis.com",
      "proxy": "http://IP:PORT"
    }
  ]
}
```

- Each entry in `accounts` = one bot instance
- Every account should use a **different proxy**
- Proxy with auth: `http://user:pass@host:port`
- Add your VPS IP to the proxy provider's whitelist

## Running

### All bots (recommended)

```bash
chmod +x manage_bots.sh
./manage_bots.sh start_all
```

### Single bot

```bash
source venv/bin/activate
HEADLESS=1 python3 bot_single.py AccountName
```

### All bots via bot_runner.py

```bash
source venv/bin/activate
HEADLESS=1 python3 bot_runner.py
```

Limit parallel instances (RAM control):

```bash
MAX_PARALLEL_BOTS=8 HEADLESS=1 python3 bot_runner.py
```

Each headless Chromium instance uses ~700–800 MB RAM in practice.  
On a 16 GB VPS you can safely run **15–18 bots** (tested: 15 bots = ~11.3 GB RAM).

## Managing bots

```bash
./manage_bots.sh status               # show status of all bots
./manage_bots.sh restart AccountName  # restart a single bot
./manage_bots.sh stop AccountName     # stop a single bot
./manage_bots.sh start AccountName    # start a single bot
./manage_bots.sh stop_all             # stop all bots
```

Logs for each bot are saved in `logs/<AccountName>.log`:

```bash
tail -f logs/AccountName.log
```

## Auto-start on VPS reboot (systemd)

```bash
nano /etc/systemd/system/grepolisbots.service
```

```ini
[Unit]
Description=Grepolis Bots
After=network.target

[Service]
WorkingDirectory=/root/grepolisbots
ExecStart=/bin/bash /root/grepolisbots/manage_bots.sh start_all
Restart=on-failure
Environment=HEADLESS=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable grepolisbots
systemctl start grepolisbots
```

## Credits

The injected userscript is **GPT-Bot-BR** by [Alexandre458](https://github.com/Alexandre458).

- Website: [https://www.gptbotbr.com](https://www.gptbotbr.com)
- Script source: [GitHub – Alexandre458/GPT-Bot-BR](https://github.com/Alexandre458/GPT-Bot-BR)

## Project structure

```
grepolisbots/
├── gpt-bot                  # Tampermonkey userscript (injected into the game)
├── accounts.json            # Account credentials, proxies, bot login (gitignored)
├── accounts.example.json    # Example config file
├── bot_runner.py            # Run all bots in a single process
├── bot_single.py            # Run a single bot by account name
├── manage_bots.sh           # Shell script to manage individual bot processes
├── requirements.txt         # Python dependencies
└── .gitignore
```
