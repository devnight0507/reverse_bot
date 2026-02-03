"""
Browser Manager - Handles Playwright browser instance with stealth mode
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger

from ..app.config import settings


class BrowserManager:
    """Manages browser lifecycle with stealth configurations"""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._session_file = settings.base_dir / "data" / "session.json"

    async def start(self) -> Page:
        """Start browser and return page"""
        logger.info("Starting browser...")

        self._playwright = await async_playwright().start()

        # Launch browser with stealth arguments
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-accelerated-2d-canvas",
                "--disable-gpu",
                "--window-size=1920,1080",
            ],
        )

        # Create context with stealth settings
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Africa/Luanda",
            geolocation={"latitude": -8.839988, "longitude": 13.289437},  # Luanda, Angola
            permissions=["geolocation"],
        )

        # Add stealth scripts
        await self._context.add_init_script("""
            // Override webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en', 'pt']
            });

            // Override platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });

            // Override hardwareConcurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });

            // Override deviceMemory
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });

            // Remove automation indicators
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

            // Override chrome
            window.chrome = {
                runtime: {},
            };

            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

        # Load saved session if exists
        await self._load_session()

        # Create new page
        self._page = await self._context.new_page()

        # Set default timeout
        self._page.set_default_timeout(30000)

        logger.info("Browser started successfully")
        return self._page

    async def stop(self):
        """Stop browser and save session"""
        logger.info("Stopping browser...")

        # Save session before closing
        await self._save_session()

        if self._page:
            await self._page.close()
            self._page = None

        if self._context:
            await self._context.close()
            self._context = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Browser stopped")

    async def _save_session(self):
        """Save browser session (cookies) to file"""
        if not self._context:
            return

        try:
            cookies = await self._context.cookies()
            session_data = {
                "cookies": cookies,
                "saved_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=4)).isoformat(),
            }

            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._session_file, "w") as f:
                json.dump(session_data, f, indent=2)

            logger.info(f"Session saved to {self._session_file}")
        except Exception as e:
            logger.error(f"Failed to save session: {e}")

    async def _load_session(self):
        """Load browser session from file"""
        if not self._session_file.exists():
            logger.info("No saved session found")
            return

        try:
            with open(self._session_file, "r") as f:
                session_data = json.load(f)

            # Check if session is expired
            expires_at = datetime.fromisoformat(session_data.get("expires_at", "2000-01-01"))
            if datetime.utcnow() > expires_at:
                logger.info("Saved session expired")
                return

            # Load cookies
            cookies = session_data.get("cookies", [])
            if cookies and self._context:
                await self._context.add_cookies(cookies)
                logger.info(f"Loaded {len(cookies)} cookies from saved session")

        except Exception as e:
            logger.error(f"Failed to load session: {e}")

    @property
    def page(self) -> Optional[Page]:
        """Get current page"""
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        """Get current context"""
        return self._context

    async def screenshot(self, name: str = "screenshot") -> Optional[Path]:
        """Take screenshot and save to file"""
        if not self._page:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        filepath = settings.screenshots_dir / filename

        try:
            settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
            await self._page.screenshot(path=str(filepath), full_page=True)
            logger.info(f"Screenshot saved: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None

    async def wait_for_navigation(self, timeout: int = 30000):
        """Wait for navigation to complete"""
        if self._page:
            await self._page.wait_for_load_state("networkidle", timeout=timeout)

    async def random_delay(self, min_ms: int = 500, max_ms: int = 1500):
        """Add random human-like delay"""
        import random
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)

    async def human_type(self, selector: str, text: str, delay: int = 50):
        """Type text with human-like delays"""
        if not self._page:
            return

        element = await self._page.wait_for_selector(selector)
        if element:
            await element.click()
            await self.random_delay(100, 300)
            for char in text:
                await self._page.keyboard.type(char, delay=delay)
                await asyncio.sleep(0.01 + 0.05 * (1 if char == " " else 0))

    async def human_click(self, selector: str):
        """Click with human-like behavior"""
        if not self._page:
            return

        element = await self._page.wait_for_selector(selector)
        if element:
            # Get element bounds
            box = await element.bounding_box()
            if box:
                import random
                # Click at random position within element
                x = box["x"] + random.uniform(5, box["width"] - 5)
                y = box["y"] + random.uniform(5, box["height"] - 5)
                await self._page.mouse.click(x, y)
            else:
                await element.click()
