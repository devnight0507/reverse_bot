"""
Browser Manager - Handles browser instance with anti-detection

Uses connect_over_cdp approach for maximum stealth:
- Chrome is launched as a normal subprocess (zero automation flags)
- Playwright connects via CDP for programmatic control
- Browser fingerprint is identical to a real Chrome installation
- No --enable-automation, no webdriver=true, no CDP launch artifacts
"""
import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger

from ..app.config import settings


class BrowserManager:
    """Manages browser lifecycle with stealth anti-detection"""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._chrome_process = None
        self._is_cdp = False
        self._session_file = settings.base_dir / "data" / "session.json"
        self._chrome_profile_dir = settings.base_dir / "data" / "chrome_profile"

    async def start(self) -> Page:
        """Start browser - uses CDP connection for best anti-detection"""
        logger.info("Starting browser...")

        self._playwright = await async_playwright().start()

        # Primary: Launch real Chrome + connect via CDP (best anti-detection)
        # Chrome launched as normal process → no automation flags → passes Cloudflare
        chrome_path = self._find_chrome()
        if chrome_path:
            try:
                await self._start_via_cdp(chrome_path)
                self._is_cdp = True
                logger.info("Connected to Chrome via CDP (stealth mode)")
            except Exception as e:
                logger.warning(f"CDP connection failed: {e}")
                await self._cleanup_chrome_process()
                self._is_cdp = False
                await self._start_regular()
        else:
            logger.info("System Chrome not found, using Playwright bundled Chromium")
            await self._start_regular()

        # Apply stealth scripts BEFORE any page navigation
        await self._apply_stealth()

        # Load saved session cookies
        await self._load_session()

        # Get or create page
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.set_default_timeout(30000)

        logger.info("Browser started successfully")
        return self._page

    async def _start_via_cdp(self, chrome_path: str):
        """Launch Chrome as subprocess and connect via CDP (undetected approach)

        Key advantage: Chrome is launched by the OS, not by Playwright.
        This means NO automation flags (--enable-automation) and NO
        Playwright-specific browser modifications. The browser fingerprint
        is identical to a real Chrome installation.
        """
        # Find a free port for remote debugging
        cdp_port = self._find_free_port()

        # Ensure profile directory exists
        self._chrome_profile_dir.mkdir(parents=True, exist_ok=True)

        # Launch Chrome as a NORMAL process - no automation flags whatsoever
        args = [
            chrome_path,
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={str(self._chrome_profile_dir)}",
            "--window-size=1920,1080",
            "--lang=pt-PT",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-popup-blocking",
            "--disable-sync",
            "--metrics-recording-only",
        ]

        if settings.headless:
            args.append("--headless=new")

        # Set timezone via environment variable
        env = os.environ.copy()
        env["TZ"] = "Africa/Luanda"

        self._chrome_process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for Chrome to start and CDP endpoint to be ready
        connected = False
        for attempt in range(15):
            await asyncio.sleep(1)
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{cdp_port}"
                )
                connected = True
                break
            except Exception:
                # Check if Chrome process died
                if self._chrome_process.poll() is not None:
                    raise RuntimeError(
                        f"Chrome process exited with code {self._chrome_process.returncode}"
                    )
                continue

        if not connected:
            raise RuntimeError("Timed out waiting for Chrome CDP endpoint")

        # Use the default browser context (from Chrome's profile)
        self._context = self._browser.contexts[0]
        logger.info(f"CDP connected on port {cdp_port}")

    async def _start_regular(self):
        """Regular Playwright launch (fallback when system Chrome not found)"""
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
            ignore_default_args=["--enable-automation"],
        )

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="pt-PT",
            timezone_id="Africa/Luanda",
            geolocation={"latitude": -8.839988, "longitude": 13.289437},
            permissions=["geolocation"],
            ignore_https_errors=True,
        )

    def _find_chrome(self) -> Optional[str]:
        """Find Chrome executable on the system"""
        system = platform.system()

        if system == "Windows":
            candidates = []
            for env_var in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"]:
                base = os.environ.get(env_var, "")
                if base:
                    candidates.append(
                        Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                    )
            for p in candidates:
                if p.exists():
                    logger.debug(f"Found Chrome at: {p}")
                    return str(p)
        elif system == "Linux":
            for name in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
                path = shutil.which(name)
                if path:
                    return path
        elif system == "Darwin":
            mac_path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            if mac_path.exists():
                return str(mac_path)

        return None

    def _find_free_port(self) -> int:
        """Find an available TCP port for CDP"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    async def _cleanup_chrome_process(self):
        """Terminate Chrome subprocess if running"""
        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
            self._chrome_process = None

    async def _apply_stealth(self):
        """Apply stealth scripts to avoid bot detection

        CDP mode needs minimal patching (Chrome is already clean).
        Regular mode needs comprehensive patching to hide automation.
        """
        if self._is_cdp:
            # Minimal stealth for CDP - Chrome is already a "real" browser
            # Only patch the few things CDP connection might affect
            await self._context.add_init_script("""
                // Insurance: ensure webdriver is not detectable
                // Should already be false since Chrome launched without --enable-automation
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Consistent languages for Angola/Portugal
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['pt-PT', 'pt', 'en-US', 'en']
                });

                // Override permissions query to match real behavior
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)
        else:
            # Comprehensive stealth for regular Playwright launch
            await self._context.add_init_script("""
                // Override webdriver - most critical check
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Fix plugins to look like real Chrome
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const plugins = [
                            {
                                name: 'Chrome PDF Plugin',
                                description: 'Portable Document Format',
                                filename: 'internal-pdf-viewer',
                                length: 1,
                            },
                            {
                                name: 'Chrome PDF Viewer',
                                description: '',
                                filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                                length: 1,
                            },
                            {
                                name: 'Native Client',
                                description: '',
                                filename: 'internal-nacl-plugin',
                                length: 2,
                            },
                        ];
                        plugins.__proto__ = PluginArray.prototype;
                        return plugins;
                    }
                });

                // Override languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['pt-PT', 'pt', 'en-US', 'en']
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

                // Override maxTouchPoints (desktop = 0)
                Object.defineProperty(navigator, 'maxTouchPoints', {
                    get: () => 0
                });

                // Remove automation indicators
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

                // Override chrome runtime
                window.chrome = {
                    runtime: {
                        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
                        PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
                        RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
                        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                    },
                    loadTimes: function() {
                        return {
                            requestTime: Date.now() / 1000 - Math.random() * 2,
                            startLoadTime: Date.now() / 1000 - Math.random(),
                            commitLoadTime: Date.now() / 1000 - Math.random() * 0.5,
                            finishDocumentLoadTime: Date.now() / 1000,
                            finishLoadTime: Date.now() / 1000,
                            firstPaintTime: Date.now() / 1000 - Math.random() * 0.3,
                            firstPaintAfterLoadTime: 0,
                            navigationType: 'Other',
                            wasFetchedViaSpdy: false,
                            wasNpnNegotiated: true,
                            npnNegotiatedProtocol: 'h2',
                            wasAlternateProtocolAvailable: false,
                            connectionInfo: 'h2',
                        };
                    },
                    csi: function() {
                        return { onloadT: Date.now(), startE: Date.now() - Math.random() * 1000, pageT: Math.random() * 3000 };
                    },
                };

                // Override permissions query
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Override WebGL vendor/renderer
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {
                    if (param === 37445) return 'Google Inc. (NVIDIA)';
                    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)';
                    return getParameter.call(this, param);
                };
            """)

    async def stop(self):
        """Stop browser, save session, and cleanup Chrome process"""
        logger.info("Stopping browser...")

        # Save session before closing
        await self._save_session()

        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        # Kill Chrome subprocess
        await self._cleanup_chrome_process()

        logger.info("Browser stopped")

    async def _save_session(self):
        """Save browser session (cookies) to file"""
        if not self._context:
            return

        try:
            cookies = await self._context.cookies()
            session_data = {
                "cookies": cookies,
                "saved_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(hours=4)).isoformat(),
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
            if datetime.now() > expires_at:
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
            await self._page.wait_for_load_state("domcontentloaded", timeout=timeout)

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
