"""
Login Automation - Handles VFS Global login process
"""
import asyncio
from typing import Optional, Tuple
from playwright.async_api import Page
from loguru import logger

from ..app.config import settings, VFSUrls, Selectors
from .browser import BrowserManager
from .turnstile import TurnstileSolver


class LoginAutomation:
    """Handles VFS Global login automation"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self.turnstile = TurnstileSolver()

    async def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Perform login to VFS Global

        Args:
            email: VFS account email (uses config if not provided)
            password: VFS account password (uses config if not provided)

        Returns:
            Tuple of (success: bool, message: str)
        """
        email = email or settings.vfs_email
        password = password or settings.vfs_password

        if not email or not password:
            return False, "Email and password are required"

        page = self.browser.page
        if not page:
            return False, "Browser not started"

        try:
            logger.info("Starting login process...")

            # Navigate to login page
            logger.info(f"Navigating to {VFSUrls.LOGIN}")
            await page.goto(VFSUrls.LOGIN, wait_until="networkidle")
            await self.browser.random_delay(1000, 2000)

            # Handle cookie consent if present
            await self._handle_cookie_consent(page)

            # Check if already logged in
            if await self._is_logged_in(page):
                logger.info("Already logged in")
                return True, "Already logged in"

            # Wait for login form
            logger.info("Waiting for login form...")
            await page.wait_for_selector(Selectors.EMAIL_INPUT, timeout=15000)

            # Enter email
            logger.info("Entering email...")
            await self.browser.human_type(Selectors.EMAIL_INPUT, email)
            await self.browser.random_delay(500, 1000)

            # Enter password
            logger.info("Entering password...")
            await self.browser.human_type(Selectors.PASSWORD_INPUT, password)
            await self.browser.random_delay(500, 1000)

            # Handle Turnstile if present
            if await self._has_turnstile(page):
                logger.info("Turnstile detected, solving...")
                token = await self.turnstile.solve(page)
                if not token:
                    await self.browser.screenshot("turnstile_failed")
                    return False, "Failed to solve Turnstile captcha"
                await self.browser.random_delay(1000, 2000)

            # Click sign in button
            logger.info("Clicking sign in button...")
            await self.browser.human_click(Selectors.SIGN_IN_BUTTON)

            # Wait for navigation or error
            await self._wait_for_login_result(page)

            # Check if login was successful
            if await self._is_logged_in(page):
                logger.info("Login successful!")
                await self.browser.screenshot("login_success")
                return True, "Login successful"
            else:
                error = await self._get_error_message(page)
                logger.error(f"Login failed: {error}")
                await self.browser.screenshot("login_failed")
                return False, f"Login failed: {error}"

        except Exception as e:
            logger.error(f"Login error: {e}")
            await self.browser.screenshot("login_error")
            return False, f"Login error: {str(e)}"

    async def logout(self) -> bool:
        """Logout from VFS Global"""
        page = self.browser.page
        if not page:
            return False

        try:
            # Look for logout button or user menu
            logout_selectors = [
                "button:has-text('Logout')",
                "button:has-text('Sign Out')",
                "a:has-text('Logout')",
                ".logout-btn",
            ]

            for selector in logout_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        await element.click()
                        await self.browser.wait_for_navigation()
                        logger.info("Logged out successfully")
                        return True
                except:
                    continue

            # If no logout button found, just clear session
            await page.context.clear_cookies()
            logger.info("Session cleared")
            return True

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False

    async def _handle_cookie_consent(self, page: Page):
        """Handle cookie consent popup if present"""
        try:
            # Try to reject all cookies for cleaner experience
            selectors = [
                Selectors.COOKIE_REJECT,
                "#onetrust-accept-btn-handler",
                "button:has-text('Reject All')",
                "button:has-text('Accept')",
            ]

            for selector in selectors:
                try:
                    button = await page.query_selector(selector)
                    if button and await button.is_visible():
                        await button.click()
                        logger.info("Cookie consent handled")
                        await self.browser.random_delay(500, 1000)
                        return
                except:
                    continue

        except Exception as e:
            logger.debug(f"Cookie consent handling: {e}")

    async def _has_turnstile(self, page: Page) -> bool:
        """Check if Turnstile captcha is present"""
        try:
            selectors = [
                Selectors.TURNSTILE_IFRAME,
                ".cf-turnstile",
                "[data-sitekey]",
            ]

            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    return True

            return False
        except:
            return False

    async def _is_logged_in(self, page: Page) -> bool:
        """Check if user is logged in"""
        try:
            # Check URL
            if "/dashboard" in page.url:
                return True

            # Check for dashboard elements
            dashboard_selectors = [
                Selectors.NEW_BOOKING_BUTTON,
                "text=Start New Booking",
                "text=My Appointments",
                ".dashboard",
            ]

            for selector in dashboard_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        return True
                except:
                    continue

            return False

        except:
            return False

    async def _wait_for_login_result(self, page: Page, timeout: int = 30000):
        """Wait for login result (success or error)"""
        try:
            await page.wait_for_function(
                """
                () => {
                    // Check for dashboard
                    if (window.location.href.includes('/dashboard')) return true;
                    // Check for error message
                    if (document.querySelector('.alert-danger')) return true;
                    if (document.querySelector('.error-message')) return true;
                    return false;
                }
                """,
                timeout=timeout,
            )
        except:
            # Timeout is ok, we'll check the result manually
            pass

        await self.browser.random_delay(1000, 2000)

    async def _get_error_message(self, page: Page) -> str:
        """Extract error message from page"""
        try:
            error_selectors = [
                Selectors.ERROR_MESSAGE,
                ".alert-danger",
                ".error-message",
                ".mat-error",
            ]

            for selector in error_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        if text:
                            return text.strip()
                except:
                    continue

            return "Unknown error"

        except:
            return "Unknown error"

    async def check_session(self) -> bool:
        """Check if current session is still valid"""
        page = self.browser.page
        if not page:
            return False

        try:
            # Navigate to dashboard
            await page.goto(VFSUrls.DASHBOARD, wait_until="networkidle")
            await self.browser.random_delay(1000, 2000)

            # Check if redirected to login
            if "/login" in page.url:
                logger.info("Session expired, need to re-login")
                return False

            return await self._is_logged_in(page)

        except Exception as e:
            logger.error(f"Session check error: {e}")
            return False
