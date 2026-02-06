"""
Login Automation - Handles VFS Global login process including OTP
"""
import asyncio
import re
from typing import Optional, Tuple, Callable
from playwright.async_api import Page
from loguru import logger

from ..app.config import settings, VFSUrls, Selectors
from .browser import BrowserManager
from .turnstile import TurnstileSolver


class LoginAutomation:
    """Handles VFS Global login automation with OTP support"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self.turnstile = TurnstileSolver()
        self._otp_callback: Optional[Callable] = None

    def set_otp_callback(self, callback: Callable):
        """Set callback for OTP retrieval (e.g., from email or Telegram)"""
        self._otp_callback = callback

    async def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Perform login to VFS Global (handles email/password + OTP)

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

            # Step 1: Navigate to "Book an appointment" page (with retry for Cloudflare 403)
            max_retries = 3
            page_loaded = False

            for attempt in range(1, max_retries + 1):
                logger.info(f"Navigating to {VFSUrls.BOOK_APPOINTMENT} (attempt {attempt}/{max_retries})")
                await page.goto(VFSUrls.BOOK_APPOINTMENT, wait_until="domcontentloaded", timeout=30000)

                # Wait for Angular SPA to fully render
                await self.browser.random_delay(5000, 8000)

                # Check if Cloudflare blocked us (403201 JSON response)
                if await self._is_blocked_page(page):
                    logger.warning(f"Cloudflare 403 detected (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        # Wait longer before retry (exponential backoff)
                        wait_time = 10 * attempt
                        logger.info(f"Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error("All attempts blocked by Cloudflare")
                        await self.browser.screenshot("cloudflare_blocked")
                        return False, "Blocked by Cloudflare protection. Try again later."

                # If session expired page, clear storage and reload
                if await self._is_session_expired_page(page):
                    logger.info("Session expired page detected, clearing storage...")
                    await self._clear_all_storage(page)
                    await page.goto(VFSUrls.BOOK_APPOINTMENT, wait_until="domcontentloaded", timeout=30000)
                    await self.browser.random_delay(5000, 8000)

                # Handle cookie consent
                await self._handle_cookie_consent(page)

                page_loaded = True
                break

            if not page_loaded:
                return False, "Failed to load VFS page after retries"

            # Step 2: Click "Book now" button to trigger Angular router navigation to /login
            # Use wait_for_selector instead of query_selector to give Angular time to render
            logger.info("Waiting for 'Book now' button...")
            book_now_selectors = [
                "a.lets-get-started",
                "a:has-text('Book now')",
                "a:has-text('Book Now')",
                "a:has-text('Reservar agora')",
            ]

            book_now_btn = None
            for selector in book_now_selectors:
                try:
                    book_now_btn = await page.wait_for_selector(selector, timeout=10000)
                    if book_now_btn:
                        logger.info(f"Book now button found with: {selector}")
                        break
                except:
                    continue

            if book_now_btn:
                await book_now_btn.click(force=True)
                logger.info("Clicked 'Book now' button, waiting for login page...")
                await self.browser.random_delay(3000, 5000)
            else:
                logger.warning("Book now button not found after waiting, trying direct login URL...")
                await page.goto(VFSUrls.LOGIN, wait_until="domcontentloaded", timeout=30000)
                await self.browser.random_delay(5000, 8000)

                # Check if direct login URL also got blocked
                if await self._is_blocked_page(page):
                    logger.error("Direct login URL also blocked by Cloudflare")
                    await self.browser.screenshot("login_blocked")
                    return False, "Blocked by Cloudflare protection. Try again later."

            logger.info(f"Current URL: {page.url}")
            await self.browser.screenshot("login_page_loaded")

            # Check if already logged in
            if await self._is_logged_in(page):
                logger.info("Already logged in")
                return True, "Already logged in"

            # Wait for login form with multiple possible selectors
            logger.info("Waiting for login form...")
            login_selectors = [
                Selectors.EMAIL_INPUT,
                "input[type='email']",
                "input[formcontrolname='username']",
                "input[formcontrolname='email']",
                "input[placeholder*='mail']",
                "input[placeholder*='Mail']",
            ]

            login_form_found = False
            for selector in login_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=5000)
                    logger.info(f"Login form found with selector: {selector}")
                    # Update the selector for this session
                    Selectors.EMAIL_INPUT = selector
                    login_form_found = True
                    break
                except:
                    continue

            if not login_form_found:
                # Take screenshot of what's actually showing
                await self.browser.screenshot("login_form_not_found")
                logger.error(f"Login form not found. Current URL: {page.url}")
                # Log page content for debugging
                title = await page.title()
                logger.error(f"Page title: {title}")
                return False, f"Login form not found. URL: {page.url}"

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
            await self.browser.random_delay(2000, 4000)

            # Check if OTP page appeared
            if await self._is_otp_page(page):
                logger.info("OTP page detected - 2FA required")
                success, message = await self._handle_otp(page)
                if not success:
                    return False, message

            # Wait for navigation result
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

    async def _is_otp_page(self, page: Page) -> bool:
        """Check if the OTP/2FA page is showing"""
        try:
            # Check for OTP-related text
            content = await page.content()
            otp_indicators = [
                "one time password",
                "otp",
                "verification code",
                "enter your code",
                "enviado por e-mail",  # Portuguese: sent by email
            ]

            content_lower = content.lower()
            for indicator in otp_indicators:
                if indicator in content_lower:
                    return True

            # Check for OTP input field
            otp_input = await page.query_selector(Selectors.OTP_INPUT)
            if otp_input and await otp_input.is_visible():
                return True

            return False
        except:
            return False

    async def _handle_otp(self, page: Page) -> Tuple[bool, str]:
        """Handle OTP/2FA step"""
        logger.info("Handling OTP verification...")
        await self.browser.screenshot("otp_page")

        # Method 1: Try to read OTP from email automatically
        otp_code = await self._read_otp_from_email()

        # Method 2: Use callback if configured (e.g., Telegram prompt)
        if not otp_code and self._otp_callback:
            logger.info("Requesting OTP via callback...")
            try:
                otp_code = await self._otp_callback()
            except Exception as e:
                logger.error(f"OTP callback error: {e}")

        # Method 3: Wait for user to enter OTP manually in the browser
        if not otp_code:
            logger.info("Waiting for manual OTP entry (120s timeout)...")
            logger.info("Please enter the OTP code in the browser window")
            success = await self._wait_for_manual_otp(page, timeout=120)
            if success:
                return True, "OTP entered manually"
            return False, "OTP timeout - no code entered within 120 seconds"

        # Enter the OTP code
        logger.info(f"Entering OTP code...")
        try:
            # Find and fill OTP input
            otp_input = await page.wait_for_selector(Selectors.OTP_INPUT, timeout=5000)
            if otp_input:
                await otp_input.click()
                await self.browser.random_delay(200, 500)
                await page.keyboard.type(otp_code, delay=80)
                await self.browser.random_delay(500, 1000)

                # Handle Turnstile on OTP page if present
                if await self._has_turnstile(page):
                    logger.info("Turnstile on OTP page, solving...")
                    await self.turnstile.solve(page)
                    await self.browser.random_delay(1000, 2000)

                # Click submit
                submit_btn = await page.query_selector(Selectors.OTP_SUBMIT)
                if submit_btn:
                    await submit_btn.click()
                    await self.browser.random_delay(2000, 4000)

                return True, "OTP submitted"
            else:
                return False, "OTP input field not found"
        except Exception as e:
            logger.error(f"OTP entry error: {e}")
            return False, f"OTP entry error: {str(e)}"

    async def _read_otp_from_email(self) -> Optional[str]:
        """Try to read OTP code from email via IMAP"""
        if not settings.smtp_user or not settings.smtp_password:
            logger.info("Email not configured, skipping auto OTP read")
            return None

        try:
            import imaplib
            import email as email_lib
            from datetime import datetime, timedelta

            logger.info("Checking email for OTP code...")

            # Connect to Gmail IMAP
            imap = imaplib.IMAP4_SSL("imap.gmail.com")
            imap.login(settings.smtp_user, settings.smtp_password)
            imap.select("INBOX")

            # Search for recent VFS emails (last 5 minutes)
            date_str = (datetime.now() - timedelta(minutes=5)).strftime("%d-%b-%Y")
            _, message_ids = imap.search(None, f'(SINCE "{date_str}" FROM "vfsglobal")')

            if not message_ids[0]:
                logger.info("No recent VFS emails found")
                imap.logout()
                return None

            # Get the latest email
            latest_id = message_ids[0].split()[-1]
            _, msg_data = imap.fetch(latest_id, "(RFC822)")

            msg = email_lib.message_from_bytes(msg_data[0][1])

            # Extract body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
                    elif part.get_content_type() == "text/html":
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

            imap.logout()

            # Extract OTP code (typically 6 digits)
            otp_match = re.search(r'\b(\d{6})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"OTP code found in email: {otp[:2]}****")
                return otp

            # Try 4-digit OTP
            otp_match = re.search(r'\b(\d{4})\b', body)
            if otp_match:
                otp = otp_match.group(1)
                logger.info(f"OTP code found in email: {otp[:2]}**")
                return otp

            logger.info("Could not extract OTP from email body")
            return None

        except Exception as e:
            logger.error(f"Failed to read OTP from email: {e}")
            return None

    async def _wait_for_manual_otp(self, page: Page, timeout: int = 120) -> bool:
        """Wait for user to manually enter OTP in the browser"""
        try:
            # Wait for the page to navigate away from OTP (user enters code manually)
            await page.wait_for_function(
                """
                () => {
                    // Check if we moved past OTP page
                    if (window.location.href.includes('/dashboard')) return true;
                    // Check for error message
                    const body = document.body.innerText.toLowerCase();
                    if (!body.includes('one time password') && !body.includes('otp')) return true;
                    return false;
                }
                """,
                timeout=timeout * 1000,
            )
            return True
        except:
            return False

    async def _is_blocked_page(self, page: Page) -> bool:
        """Check if Cloudflare/VFS WAF blocked the request (403201 JSON response)"""
        try:
            content = await page.content()
            content_lower = content.lower()

            # Check for 403201 JSON response
            if '"403201"' in content or '"code":"403201"' in content.replace(" ", ""):
                return True

            # Check for Cloudflare block page indicators
            if "access denied" in content_lower and "cloudflare" in content_lower:
                return True

            # Check for empty/minimal page (Cloudflare may return very little HTML)
            body_text = await page.evaluate("() => document.body?.innerText?.trim() || ''")
            if body_text and body_text.startswith('{"code"'):
                return True

            return False
        except:
            return False

    async def _is_session_expired_page(self, page: Page) -> bool:
        """Check if current page is the 'Session Expired' or 'page-not-found' page"""
        try:
            url = page.url.lower()
            if "page-not-found" in url:
                return True

            content = await page.content()
            content_lower = content.lower()
            expired_indicators = [
                "session expired",
                "session invalid",
                "sessão expirada",
                "go back to home",
            ]
            for indicator in expired_indicators:
                if indicator in content_lower:
                    return True

            return False
        except:
            return False

    async def _clear_all_storage(self, page: Page):
        """Clear cookies, localStorage, and sessionStorage to avoid stale session issues"""
        try:
            # Clear cookies
            await page.context.clear_cookies()

            # Clear localStorage and sessionStorage via JS
            await page.evaluate("""
                () => {
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}
                }
            """)

            # Delete old session file
            session_file = self.browser._session_file
            if session_file.exists():
                import os
                os.remove(session_file)

            logger.info("Cleared all browser storage")
        except Exception as e:
            logger.debug(f"Storage cleanup: {e}")

    async def logout(self) -> bool:
        """Logout from VFS Global"""
        page = self.browser.page
        if not page:
            return False

        try:
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

            await page.context.clear_cookies()
            logger.info("Session cleared")
            return True

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False

    async def _handle_cookie_consent(self, page: Page):
        """Handle cookie consent popup if present"""
        try:
            # Wait for cookie banner to appear
            try:
                await page.wait_for_selector("#onetrust-banner-sdk", timeout=5000)
                logger.info("Cookie banner detected")
            except:
                logger.debug("No cookie banner found")
                return

            selectors = [
                "#onetrust-accept-btn-handler",        # Aceitar todos os cookies (confirmed)
                "#onetrust-reject-all-handler",        # Rejeitar Todos
                "button:has-text('Aceitar todos')",    # Portuguese accept
                "button:has-text('Rejeitar Todos')",   # Portuguese reject
            ]

            for selector in selectors:
                try:
                    button = await page.query_selector(selector)
                    if button:
                        await button.click(force=True)
                        logger.info(f"Cookie consent handled with: {selector}")
                        await self.browser.random_delay(1000, 2000)
                        return
                except:
                    continue

            logger.warning("Cookie banner found but no buttons matched")

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
            if "/dashboard" in page.url:
                return True

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
                    if (window.location.href.includes('/dashboard')) return true;
                    if (document.querySelector('.alert-danger')) return true;
                    if (document.querySelector('.error-message')) return true;
                    return false;
                }
                """,
                timeout=timeout,
            )
        except:
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
            await page.goto(VFSUrls.DASHBOARD, wait_until="domcontentloaded", timeout=30000)
            await self.browser.random_delay(1000, 2000)

            if "/login" in page.url:
                logger.info("Session expired, need to re-login")
                return False

            return await self._is_logged_in(page)

        except Exception as e:
            logger.error(f"Session check error: {e}")
            return False
