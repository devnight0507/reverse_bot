"""
Notification Service - Handles Telegram and Email notifications
"""
import asyncio
from typing import Optional
from datetime import datetime
from loguru import logger

from ..app.config import settings


class NotificationService:
    """Handles notifications via Telegram and Email"""

    def __init__(self):
        self.telegram_enabled = bool(settings.telegram_bot_token and settings.telegram_chat_id)
        self.email_enabled = bool(settings.smtp_user and settings.smtp_password)

    async def notify(
        self,
        message: str,
        title: str = "VFS Bot Notification",
        priority: str = "normal"
    ):
        """Send notification via all enabled channels"""
        results = []

        if self.telegram_enabled:
            success = await self.send_telegram(message, title)
            results.append(("telegram", success))

        if self.email_enabled:
            success = await self.send_email(message, title)
            results.append(("email", success))

        return results

    async def send_telegram(
        self,
        message: str,
        title: Optional[str] = None
    ) -> bool:
        """Send Telegram notification"""
        if not self.telegram_enabled:
            logger.warning("Telegram not configured")
            return False

        try:
            import httpx

            # Format message (plain text - no Markdown to avoid parse errors)
            if title:
                text = f"[ {title} ]\n\n{message}"
            else:
                text = message

            # Add timestamp
            text += f"\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": text,
                    },
                    timeout=10,
                )

                if response.status_code == 200:
                    logger.info("Telegram notification sent")
                    return True
                else:
                    logger.error(f"Telegram error: {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")
            return False

    async def send_email(
        self,
        message: str,
        subject: str = "VFS Bot Notification"
    ) -> bool:
        """Send Email notification"""
        if not self.email_enabled:
            logger.warning("Email not configured")
            return False

        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            # Create message
            msg = MIMEMultipart()
            msg["From"] = settings.smtp_user
            msg["To"] = settings.smtp_user  # Send to self
            msg["Subject"] = subject

            # HTML body
            html = f"""
            <html>
            <body>
                <h2>{subject}</h2>
                <p>{message.replace(chr(10), '<br>')}</p>
                <hr>
                <small>Sent at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small>
            </body>
            </html>
            """
            msg.attach(MIMEText(html, "html"))

            # Send email
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=True,
            )

            logger.info("Email notification sent")
            return True

        except Exception as e:
            logger.error(f"Email notification failed: {e}")
            return False

    async def notify_slot_found(self, dates: list, applicant_name: str = None):
        """Send notification when slot is found"""
        dates_str = ", ".join([d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in dates[:5]])
        if len(dates) > 5:
            dates_str += f" (+{len(dates) - 5} more)"

        message = f"SLOTS AVAILABLE!\n\nDates: {dates_str}"
        if applicant_name:
            message += f"\n\nApplicant: {applicant_name}"

        await self.notify(message, title="VFS Slot Found!", priority="high")

    async def notify_booking_success(
        self,
        applicant_name: str,
        appointment_date: str,
        appointment_time: str = None,
        confirmation_code: str = None
    ):
        """Send notification when booking is successful"""
        message = f"Booking Successful!\n\n"
        message += f"Applicant: {applicant_name}\n"
        message += f"Date: {appointment_date}\n"
        if appointment_time:
            message += f"Time: {appointment_time}\n"
        if confirmation_code:
            message += f"\nConfirmation Code: {confirmation_code}"

        await self.notify(message, title="VFS Booking Success!", priority="high")

    async def notify_booking_failed(self, applicant_name: str, reason: str):
        """Send notification when booking fails"""
        message = f"Booking Failed!\n\n"
        message += f"Applicant: {applicant_name}\n"
        message += f"Reason: {reason}"

        await self.notify(message, title="VFS Booking Failed", priority="high")

    async def notify_error(self, error: str, context: str = None):
        """Send notification on error"""
        message = f"Error occurred: {error}"
        if context:
            message += f"\n\nContext: {context}"

        await self.notify(message, title="VFS Bot Error", priority="normal")

    async def test_connection(self) -> dict:
        """Test notification channels"""
        results = {
            "telegram": {"enabled": self.telegram_enabled, "working": False},
            "email": {"enabled": self.email_enabled, "working": False},
        }

        if self.telegram_enabled:
            results["telegram"]["working"] = await self.send_telegram("Test message from VFS Bot")

        if self.email_enabled:
            results["email"]["working"] = await self.send_email("Test message from VFS Bot", "VFS Bot Test")

        return results
