import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"
USERSCRIPT_FILE = Path(__file__).parent / "gpt-bot"

GREPOLIS_LOGIN_URL = "https://pl.grepolis.com/start"
BOT_LOGIN_TIMEOUT = 30_000
PAGE_TIMEOUT      = 60_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def load_config(login: str) -> tuple[dict, str, str, str]:
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    account = next((a for a in data["accounts"] if a["grepolis_login"] == login), None)
    if not account:
        print(f"Account not found: {login}")
        sys.exit(1)
    return account, data["bot_login"], data["bot_password"], data["world"]


def load_userscript() -> str:
    raw = USERSCRIPT_FILE.read_text(encoding="utf-8")
    raw = re.sub(r"//\s*==UserScript==.*?//\s*==/UserScript==", "", raw, flags=re.DOTALL)
    return raw.strip()


def parse_proxy(proxy_url: str) -> dict:
    match = re.match(
        r"https?://(?:(?P<user>[^:@]+):(?P<password>[^@]+)@)?(?P<server>[^/]+)",
        proxy_url,
    )
    if not match:
        raise ValueError(f"Invalid proxy format: {proxy_url}")
    result = {"server": f"http://{match.group('server')}"}
    if match.group("user"):
        result["username"] = match.group("user")
        result["password"] = match.group("password")
    return result


async def run_bot(account: dict, bot_login: str, bot_password: str, world: str):
    log = logging.getLogger(account["grepolis_login"])
    proxy_cfg = parse_proxy(account["proxy"])

    headless = os.environ.get("HEADLESS", "1") != "0"
    args = [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-extensions", "--disable-background-networking",
        "--disable-sync", "--disable-translate", "--mute-audio",
        "--disable-default-apps", "--disable-hang-monitor",
        "--disable-prompt-on-repost", "--disable-client-side-phishing-detection",
        "--disable-component-update", "--disable-breakpad",
        "--disable-ipc-flooding-protection", "--renderer-process-limit=1",
        "--disable-blink-features=AutomationControlled",
        "--disable-accelerated-2d-canvas", "--disable-accelerated-video-decode",
        "--num-raster-threads=1", "--disable-threaded-animation",
        "--disable-threaded-scrolling", "--disable-checker-imaging",
        "--disable-image-animation-resync",
    ]
    if headless:
        args.append("--headless=new")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=args)
        context = await browser.new_context(
            proxy=proxy_cfg,
            viewport={"width": 800, "height": 600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )

        await context.add_init_script(script="Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        userscript_code = load_userscript()
        await context.add_init_script(script=userscript_code)

        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)

        async def block_resources(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            elif route.request.resource_type == "stylesheet" and "innogamescdn" in route.request.url:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_resources)

        try:
            log.info("Opening Grepolis login page…")
            await page.goto(GREPOLIS_LOGIN_URL, wait_until="domcontentloaded")

            log.info("Logging in to Grepolis…")
            try:
                await page.click("button#onetrust-accept-btn-handler", timeout=5_000)
            except PlaywrightTimeout:
                pass

            await page.wait_for_selector('#login_form_inner #name', timeout=PAGE_TIMEOUT)
            await page.fill('#login_form_inner #name', account["grepolis_login"])
            await page.fill('#login_form_inner #password', account["grepolis_password"])
            await page.evaluate("submit_form_light('loginform')")

            world_name = account.get("world", world)
            await page.wait_for_selector(
                f'li.world_name[data-worldname="{world_name}"], div#wrapper_all',
                timeout=PAGE_TIMEOUT,
            )

            if "/game/" not in page.url:
                log.info("World selection screen – clicking '%s'…", world_name)
                await page.click(f'li.world_name[data-worldname="{world_name}"]')
                await page.wait_for_url(
                    f"**/{account['grepolis_url'].split('//')[1]}/game/**",
                    timeout=PAGE_TIMEOUT,
                )
            log.info("Game loaded.")

            log.info("Waiting for bot panel…")
            await page.wait_for_selector('#login_user', state='visible', timeout=BOT_LOGIN_TIMEOUT)
            await page.fill('#login_user', bot_login)
            await page.fill('#login_pass', bot_password)
            await page.click('#login_btn')
            log.info("Bot active!")

            while True:
                await asyncio.sleep(60)
                try:
                    await page.evaluate("1 + 1")
                except Exception as e:
                    log.warning("Lost connection: %s", e)
                    break

        except Exception as e:
            log.error("Error: %s", e, exc_info=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            log.info("Browser closed.")


async def main_with_retry():
    if len(sys.argv) < 2:
        print("Usage: python bot_single.py <grepolis_login>")
        sys.exit(1)

    login = sys.argv[1]
    account, bot_login, bot_password, world = load_config(login)
    log = logging.getLogger(login)
    attempt = 0
    max_wait = 300

    while True:
        attempt += 1
        log.info("=== Attempt #%d ===", attempt)
        await run_bot(account, bot_login, bot_password, world)
        wait = min(30 * attempt, max_wait)
        log.info("Bot stopped. Restarting in %ds…", wait)
        await asyncio.sleep(wait)


if __name__ == "__main__":
    asyncio.run(main_with_retry())
