"""
Turnstile Solver - Handles Cloudflare Turnstile captcha solving via 2Captcha
"""
import asyncio
import httpx
from typing import Optional
from playwright.async_api import Page
from loguru import logger

from ..app.config import settings


class TurnstileSolver:
    """Solves Cloudflare Turnstile captcha using 2Captcha API"""

    API_URL = "https://2captcha.com"
    POLL_INTERVAL = 5  # seconds
    MAX_ATTEMPTS = 24  # 2 minutes max wait

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.captcha_api_key
        if not self.api_key:
            logger.warning("2Captcha API key not configured")

    async def solve(self, page: Page, timeout: int = 120) -> Optional[str]:
        """
        Solve Turnstile captcha on the current page

        Args:
            page: Playwright page instance
            timeout: Maximum time to wait for solution (seconds)

        Returns:
            Captcha token if solved, None otherwise
        """
        if not self.api_key:
            logger.error("Cannot solve Turnstile: API key not configured")
            return None

        try:
            # Get sitekey from page
            sitekey = await self._get_sitekey(page)
            if not sitekey:
                logger.warning("No Turnstile sitekey found on page")
                return None

            page_url = page.url
            logger.info(f"Solving Turnstile for {page_url} with sitekey {sitekey[:20]}...")

            # Submit captcha to 2Captcha
            task_id = await self._create_task(sitekey, page_url)
            if not task_id:
                logger.error("Failed to create 2Captcha task")
                return None

            logger.info(f"2Captcha task created: {task_id}")

            # Poll for result
            token = await self._poll_result(task_id, timeout)
            if token:
                logger.info("Turnstile solved successfully")
                # Inject the token into the page
                await self._inject_token(page, token)
                return token
            else:
                logger.error("Failed to solve Turnstile within timeout")
                return None

        except Exception as e:
            logger.error(f"Turnstile solving error: {e}")
            return None

    async def _get_sitekey(self, page: Page) -> Optional[str]:
        """Extract Turnstile sitekey from page"""
        try:
            # Try multiple methods to find sitekey
            selectors = [
                "[data-sitekey]",
                ".cf-turnstile[data-sitekey]",
                "iframe[src*='challenges.cloudflare.com']",
            ]

            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        # Try to get sitekey attribute
                        sitekey = await element.get_attribute("data-sitekey")
                        if sitekey:
                            return sitekey

                        # Try to extract from iframe src
                        src = await element.get_attribute("src")
                        if src and "sitekey=" in src:
                            import re
                            match = re.search(r"sitekey=([a-zA-Z0-9_-]+)", src)
                            if match:
                                return match.group(1)
                except:
                    continue

            # Try JavaScript extraction
            sitekey = await page.evaluate("""
                () => {
                    // Try window.turnstile
                    if (window.turnstile && window.turnstile.getResponse) {
                        const widgets = document.querySelectorAll('[data-sitekey]');
                        if (widgets.length > 0) {
                            return widgets[0].getAttribute('data-sitekey');
                        }
                    }
                    // Try finding in page source
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const match = script.textContent.match(/sitekey['"\\s:]+['"]([a-zA-Z0-9_-]+)['"]/);
                        if (match) return match[1];
                    }
                    return null;
                }
            """)

            return sitekey

        except Exception as e:
            logger.error(f"Error extracting sitekey: {e}")
            return None

    async def _create_task(self, sitekey: str, page_url: str) -> Optional[str]:
        """Create 2Captcha task for Turnstile"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.API_URL}/in.php",
                    data={
                        "key": self.api_key,
                        "method": "turnstile",
                        "sitekey": sitekey,
                        "pageurl": page_url,
                        "json": 1,
                    },
                    timeout=30,
                )
                result = response.json()

                if result.get("status") == 1:
                    return result.get("request")
                else:
                    logger.error(f"2Captcha error: {result.get('request')}")
                    return None

        except Exception as e:
            logger.error(f"Error creating 2Captcha task: {e}")
            return None

    async def _poll_result(self, task_id: str, timeout: int) -> Optional[str]:
        """Poll 2Captcha for task result"""
        max_attempts = min(self.MAX_ATTEMPTS, timeout // self.POLL_INTERVAL)

        async with httpx.AsyncClient() as client:
            for attempt in range(max_attempts):
                await asyncio.sleep(self.POLL_INTERVAL)

                try:
                    response = await client.get(
                        f"{self.API_URL}/res.php",
                        params={
                            "key": self.api_key,
                            "action": "get",
                            "id": task_id,
                            "json": 1,
                        },
                        timeout=30,
                    )
                    result = response.json()

                    if result.get("status") == 1:
                        return result.get("request")
                    elif result.get("request") == "CAPCHA_NOT_READY":
                        logger.debug(f"Captcha not ready, attempt {attempt + 1}/{max_attempts}")
                        continue
                    else:
                        logger.error(f"2Captcha error: {result.get('request')}")
                        return None

                except Exception as e:
                    logger.error(f"Error polling 2Captcha: {e}")
                    continue

        return None

    async def _inject_token(self, page: Page, token: str):
        """Inject solved token into the page"""
        try:
            # Try multiple injection methods
            await page.evaluate(f"""
                (token) => {{
                    // Method 1: Set response in turnstile widget
                    const responseInputs = document.querySelectorAll('[name="cf-turnstile-response"]');
                    responseInputs.forEach(input => {{
                        input.value = token;
                    }});

                    // Method 2: Set in hidden input
                    const hiddenInputs = document.querySelectorAll('input[type="hidden"]');
                    hiddenInputs.forEach(input => {{
                        if (input.name && input.name.includes('turnstile')) {{
                            input.value = token;
                        }}
                    }});

                    // Method 3: Trigger callback if exists
                    if (window.turnstileCallback) {{
                        window.turnstileCallback(token);
                    }}

                    // Method 4: Dispatch event
                    document.dispatchEvent(new CustomEvent('turnstile-solved', {{ detail: token }}));
                }}
            """, token)

            logger.info("Turnstile token injected")

        except Exception as e:
            logger.error(f"Error injecting token: {e}")

    async def report_bad(self, task_id: str):
        """Report incorrect captcha solution"""
        try:
            async with httpx.AsyncClient() as client:
                await client.get(
                    f"{self.API_URL}/res.php",
                    params={
                        "key": self.api_key,
                        "action": "reportbad",
                        "id": task_id,
                    },
                    timeout=10,
                )
                logger.info(f"Reported bad captcha: {task_id}")
        except Exception as e:
            logger.error(f"Error reporting bad captcha: {e}")

    async def get_balance(self) -> Optional[float]:
        """Get 2Captcha account balance"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.API_URL}/res.php",
                    params={
                        "key": self.api_key,
                        "action": "getbalance",
                        "json": 1,
                    },
                    timeout=10,
                )
                result = response.json()

                if result.get("status") == 1:
                    return float(result.get("request", 0))
                return None

        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return None
