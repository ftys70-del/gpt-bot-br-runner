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
BOT_LOGIN_TIMEOUT = 30_000   # ms – timeout waiting for the bot login panel
PAGE_TIMEOUT     = 60_000   # ms – general page timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def load_config() -> tuple[list[dict], str, str, str]:
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data["accounts"], data["bot_login"], data["bot_password"], data["world"]


def load_userscript() -> str:
    """Loads the Tampermonkey userscript and strips the ==UserScript== metadata block."""
    raw = USERSCRIPT_FILE.read_text(encoding="utf-8")
    # Strip Tampermonkey metadata block (not valid JS)
    raw = re.sub(r"//\s*==UserScript==.*?//\s*==/UserScript==", "", raw, flags=re.DOTALL)
    return raw.strip()


def parse_proxy(proxy_url: str) -> dict:
    """Converts 'http://host:port' or 'http://user:pass@host:port' to a Playwright proxy dict."""
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


# ---------------------------------------------------------------------------
# Core logic for a single bot instance
# ---------------------------------------------------------------------------
async def run_bot(account: dict, bot_login: str, bot_password: str, world: str, playwright, semaphore: asyncio.Semaphore, counter: dict | None = None, total: int = 0):
    log = logging.getLogger(account["grepolis_login"])
    proxy_cfg = parse_proxy(account["proxy"])

    async with semaphore:
        log.info("Launching browser (proxy: %s)", proxy_cfg["server"])

        headless = os.environ.get("HEADLESS", "1") != "0"
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--mute-audio",
            "--disable-default-apps",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-client-side-phishing-detection",
            "--disable-component-update",
            "--disable-breakpad",
            "--disable-ipc-flooding-protection",
            "--renderer-process-limit=1",
            "--disable-blink-features=AutomationControlled",
            "--disable-accelerated-2d-canvas",
            "--disable-accelerated-video-decode",
            "--num-raster-threads=1",
            "--disable-threaded-animation",
            "--disable-threaded-scrolling",
            "--disable-checker-imaging",
            "--disable-image-animation-resync",
        ]
        if headless:
            args.append("--headless=new")
        browser = await playwright.chromium.launch(
            headless=False,
            args=args,
        )

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
            # ------------------------------------------------------------------
            # 1. Open login page – bot userscript runs automatically
            # ------------------------------------------------------------------
            log.info("Opening Grepolis login page…")
            await page.goto(GREPOLIS_LOGIN_URL, wait_until="domcontentloaded")

            # ------------------------------------------------------------------
            # 2. Log in to Grepolis
            # ------------------------------------------------------------------
            log.info("Logging in to Grepolis…")

            # Accept cookies banner if it appears
            try:
                await page.click("button#onetrust-accept-btn-handler", timeout=5_000)
                log.info("Cookies accepted.")
            except PlaywrightTimeout:
                pass

            await page.wait_for_selector('#login_form_inner #name', timeout=PAGE_TIMEOUT)
            await page.fill('#login_form_inner #name', account["grepolis_login"])
            await page.fill('#login_form_inner #password', account["grepolis_password"])
            await page.evaluate("submit_form_light('loginform')")

            log.info("Login submitted – waiting for world selection or game…")

            world_name = account.get("world", world)

            await page.wait_for_selector(
                f'li.world_name[data-worldname="{world_name}"], div#wrapper_all',
                timeout=PAGE_TIMEOUT,
            )

            if "/game/" in page.url:
                log.info("Already in game (auto-redirect).")
            else:
                log.info("World selection screen – clicking '%s'…", world_name)
                await page.click(f'li.world_name[data-worldname="{world_name}"]')
                log.info("Clicked '%s' – waiting for game to load…", world_name)
                await page.wait_for_url(
                    f"**/{account['grepolis_url'].split('//')[1]}/game/**",
                    timeout=PAGE_TIMEOUT,
                )
                log.info("Game loaded.")

            # ------------------------------------------------------------------
            # 3. Log in to the bot panel
            # ------------------------------------------------------------------
            log.info("Waiting for bot panel (#login_user)…")
            await page.wait_for_selector('#login_user', state='visible', timeout=BOT_LOGIN_TIMEOUT)
            log.info("Bot panel visible – entering credentials…")
            await page.fill('#login_user', bot_login)
            await page.fill('#login_pass', bot_password)
            await page.click('#login_btn')
            if counter is not None:
                counter["active"] += 1
                log.info("Bot active! [%d/%d]", counter["active"], total)
            else:
                log.info("Bot active!")

            # ------------------------------------------------------------------
            # 4. Keep session alive (infinite heartbeat loop)
            # ------------------------------------------------------------------
            log.info("Bot running. Keeping session alive…")
            while True:
                await asyncio.sleep(60)
                try:
                    # Simple heartbeat – check if page is still alive
                    await page.evaluate("1 + 1")
                except Exception as e:
                    log.warning("Lost connection to page: %s", e)
                    break

        except Exception as e:
            log.error("Error: %s", e, exc_info=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            log.info("Browser closed.")


async def run_bot_with_retry(account: dict, bot_login: str, bot_password: str, world: str, playwright, semaphore: asyncio.Semaphore, counter: dict | None = None, total: int = 0):
    log = logging.getLogger(account["grepolis_login"])
    attempt = 0
    max_wait = 300  # max 5 minutes between restarts

    while True:
        attempt += 1
        log.info("=== Attempt #%d ===", attempt)
        await run_bot(account, bot_login, bot_password, world, playwright, semaphore, counter, total)
        wait = min(30 * attempt, max_wait)
        log.info("Bot stopped. Restarting in %ds…", wait)
        await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    accounts, bot_login, bot_password, world = load_config()
    if not accounts:
        print("No accounts found in accounts.json")
        sys.exit(1)

    total = len(accounts)
    counter = {"active": 0}
    main_log = logging.getLogger("main")
    main_log.info("Starting %d bots…", total)

    # Semaphore limits the number of parallel browser instances
    # Each headless Chromium uses ~150–250 MB RAM
    # On a 16 GB RAM VPS you can safely set 40–50
    max_parallel = int(os.environ.get("MAX_PARALLEL_BOTS", total))
    semaphore = asyncio.Semaphore(max_parallel)

    async with async_playwright() as playwright:
        tasks = []
        for i, account in enumerate(accounts):
            await asyncio.sleep(i * 5)  # 5 second stagger between each bot
            tasks.append(asyncio.ensure_future(
                run_bot_with_retry(account, bot_login, bot_password, world, playwright, semaphore, counter, total)
            ))
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
