"""
Identity Verification - Handles face liveness + passport verification on idnvui.vfsglobal.com

VFS Global uses a camera-based identity verification system:
1. Face liveness check (camera captures face, "Move closer", "Hold still", color overlay scanning)
2. Passport front capture (camera)
3. Passport photo page capture (camera + 10-second countdown)

Approaches for automation:
A. Chrome fake video flags (--use-file-for-fake-video-capture=file.mjpeg)
B. pyvirtualcam + OBS Virtual Camera (Windows - dynamic video switching)
C. JavaScript getUserMedia override (canvas.captureStream)
D. Hybrid: pause bot, notify human, wait for manual completion

This module primarily implements approach D (hybrid) as the safe default,
with support for approach A (Chrome flags) configured at browser startup.
"""
import asyncio
from pathlib import Path
from typing import Optional, Tuple, Callable
from playwright.async_api import Page
from loguru import logger

from ..app.config import settings


class IdentityVerificationHandler:
    """Handles identity verification flow on idnvui.vfsglobal.com"""

    def __init__(self, page: Page, notification_callback: Optional[Callable] = None):
        self.page = page
        self._notify = notification_callback
        self._videos_dir = settings.base_dir / "data" / "videos"

    async def handle(self) -> Tuple[bool, str]:
        """Main handler - detect and process identity verification

        Returns:
            (success, message)
        """
        if "idnvui.vfsglobal.com" not in self.page.url:
            return True, "No identity verification needed"

        logger.info("Identity verification page detected")

        # Notify human (via Telegram/callback)
        await self._send_notification(
            "Identity verification required!\n"
            "Please complete face + passport verification on the computer.\n"
            "The bot will resume automatically after verification."
        )

        # Try to auto-navigate through verification steps
        # Step 1: Click Continue on start page
        await self._click_continue_on_start_page()

        # Step 2: Wait for face liveness to complete (human or fake video)
        await self._wait_for_face_liveness()

        # Step 3: Wait for passport capture
        await self._wait_for_passport_capture()

        # Step 4: Wait for security check completed
        success = await self._wait_for_completion()

        if success:
            # Click Continue to redirect back to VFS
            await self._click_redirect_continue()
            logger.info("Identity verification completed successfully")
            return True, "Verification completed"
        else:
            logger.error("Identity verification failed or timed out")
            return False, "Verification failed or timed out"

    async def _click_continue_on_start_page(self):
        """Click Continue button on the verification start/instructions page"""
        try:
            # Wait for the start page to load
            await asyncio.sleep(2)

            body_text = await self.page.text_content("body")
            if body_text and "start identity verification" in body_text.lower():
                logger.info("On verification start page")

                continue_btn = await self.page.query_selector(
                    "button:has-text('CONTINUE'), button:has-text('Continue')"
                )
                if continue_btn:
                    await continue_btn.click()
                    logger.info("Clicked Continue on verification start page")
                    await asyncio.sleep(3)
        except Exception as e:
            logger.debug(f"Start page continue: {e}")

    async def _wait_for_face_liveness(self):
        """Wait for face liveness check to complete

        If using Chrome fake video flags, this should complete automatically.
        Otherwise, waits for human to complete it.
        """
        try:
            logger.info("Waiting for face liveness check...")

            # Wait for camera UI to appear and then complete
            # The page shows: "Move face in front of camera" → "Move closer" → "Hold still" → "Verifying..."
            # Then either "Photo Accepted" (success) or "Photo Rejected" (retry needed)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.page.wait_for_function(
                        """() => {
                            const text = document.body?.innerText?.toLowerCase() || '';
                            return text.includes('photo accepted') ||
                                   text.includes('photo rejected') ||
                                   text.includes('start passport') ||
                                   text.includes('passport verification');
                        }""",
                        timeout=120000,  # 2 minutes per attempt
                    )

                    body_text = await self.page.text_content("body")
                    if body_text and "photo rejected" in body_text.lower():
                        logger.warning(f"Face liveness failed (attempt {attempt + 1}/{max_retries})")

                        # Click RETRY button
                        retry_btn = await self.page.query_selector(
                            "button:has-text('RETRY'), button:has-text('Retry')"
                        )
                        if retry_btn:
                            await retry_btn.click()
                            await asyncio.sleep(3)
                            continue
                    else:
                        logger.info("Face liveness check passed")
                        return

                except Exception:
                    logger.warning(f"Face liveness timeout (attempt {attempt + 1})")

            logger.error("Face liveness failed after all retries")

        except Exception as e:
            logger.error(f"Face liveness error: {e}")

    async def _wait_for_passport_capture(self):
        """Wait for passport front + photo page capture to complete"""
        try:
            logger.info("Waiting for passport capture...")

            # Click Continue if on passport start page
            body_text = await self.page.text_content("body")
            if body_text and "start passport verification" in body_text.lower():
                continue_btn = await self.page.query_selector(
                    "button:has-text('CONTINUE'), button:has-text('Continue')"
                )
                if continue_btn:
                    await continue_btn.click()
                    await asyncio.sleep(3)

            # Wait for passport detection and capture
            # Flow: "Display front of passport" → "Open passport to display the photo page"
            #       → "Passport Detected" → "CAPTURE" (10s countdown) → Complete
            await self.page.wait_for_function(
                """() => {
                    const text = document.body?.innerText?.toLowerCase() || '';
                    return text.includes('security check completed') ||
                           text.includes('passport detected') ||
                           text.includes('you will be automatically redirected');
                }""",
                timeout=180000,  # 3 minutes
            )

            # If passport detected, may need to click CAPTURE
            body_text = await self.page.text_content("body")
            if body_text and "passport detected" in body_text.lower():
                capture_btn = await self.page.query_selector(
                    "button:has-text('CAPTURE'), button:has-text('Capture')"
                )
                if capture_btn:
                    await capture_btn.click()
                    logger.info("Clicked CAPTURE for passport photo page")
                    await asyncio.sleep(15)  # 10-second countdown + buffer

            logger.info("Passport capture completed")

        except Exception as e:
            logger.error(f"Passport capture error: {e}")

    async def _wait_for_completion(self) -> bool:
        """Wait for 'SECURITY CHECK COMPLETED' message"""
        try:
            logger.info("Waiting for security check completion...")

            await self.page.wait_for_function(
                """() => {
                    const text = document.body?.innerText?.toLowerCase() || '';
                    return text.includes('security check completed') ||
                           window.location.href.includes('visa.vfsglobal.com');
                }""",
                timeout=60000,  # 1 minute
            )

            logger.info("Security check completed")
            return True

        except Exception:
            logger.warning("Security check completion timeout")
            return False

    async def _click_redirect_continue(self):
        """Click Continue button to redirect back to VFS after verification"""
        try:
            # Auto-redirect should happen in 5 seconds, but click Continue as backup
            await asyncio.sleep(2)

            if "idnvui.vfsglobal.com" in self.page.url:
                continue_btn = await self.page.query_selector(
                    "button:has-text('CONTINUE'), button:has-text('Continue')"
                )
                if continue_btn:
                    await continue_btn.click()
                    logger.info("Clicked Continue for redirect")
                    await asyncio.sleep(5)

            # Wait for redirect back to VFS
            try:
                await self.page.wait_for_function(
                    "() => window.location.href.includes('visa.vfsglobal.com')",
                    timeout=15000,
                )
                logger.info("Redirected back to VFS")
            except:
                logger.warning("Redirect back to VFS may not have completed")

        except Exception as e:
            logger.debug(f"Redirect continue: {e}")

    async def _send_notification(self, message: str):
        """Send notification via callback"""
        if self._notify:
            try:
                if asyncio.iscoroutinefunction(self._notify):
                    await self._notify("verification_needed", message)
                else:
                    self._notify("verification_needed", message)
            except Exception as e:
                logger.error(f"Notification error: {e}")
        logger.info(f"NOTIFICATION: {message}")


def get_fake_video_chrome_args(video_path: Optional[str] = None) -> list:
    """Get Chrome launch arguments for fake video camera feed

    Args:
        video_path: Path to MJPEG or Y4M video file.
                   If None, returns args for test pattern (animated bars).

    Returns:
        List of Chrome args to add to launch command
    """
    args = [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
    ]

    if video_path:
        path = Path(video_path)
        if path.exists():
            args.append(f"--use-file-for-fake-video-capture={path}")
            logger.info(f"Fake video configured: {path}")
        else:
            logger.warning(f"Video file not found: {path}")

    return args
