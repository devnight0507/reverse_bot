#!/usr/bin/env python3
"""
VFS Booking Bot - Main Entry Point
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger

from src.app.config import settings
from src.app.database import init_db


def setup_logging():
    """Configure logging"""
    log_file = settings.logs_dir / "vfs_bot.log"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(
        log_file,
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    )


async def init():
    """Initialize application"""
    setup_logging()
    logger.info("Initializing VFS Booking Bot...")

    # Create directories
    settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    # Initialize database
    await init_db()
    logger.info("Database initialized")


def run_api():
    """Run API server"""
    setup_logging()
    logger.info(f"Starting API server on {settings.api_host}:{settings.api_port}")

    uvicorn.run(
        "src.app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level="info",
    )


async def run_bot():
    """Run bot in standalone mode"""
    from src.automation.browser import BrowserManager
    from src.automation.login import LoginAutomation
    from src.automation.monitor import SlotMonitor
    from src.services.notification import NotificationService

    await init()

    notification = NotificationService()
    browser = BrowserManager()

    async def on_slot_found(event, data):
        if event == "slot_found":
            await notification.notify_slot_found(data["dates"])
        elif event == "booking_success":
            applicant = data["applicant"]
            await notification.notify_booking_success(
                f"{applicant['first_name']} {applicant['last_name']}",
                str(data.get("date", "Unknown")),
                confirmation_code=data.get("code"),
            )

    async def on_error(event, data):
        await notification.notify_error(str(data), event)

    try:
        page = await browser.start()
        monitor = SlotMonitor(browser, on_slot_found=on_slot_found, on_error=on_error)

        # Example applicant data - in production, load from database
        applicants = [
            {
                "first_name": "Test",
                "last_name": "User",
                "email": "test@example.com",
                "phone": "+244123456789",
                "passport_number": "AB123456",
                "passport_expiry": "2030-01-01",
                "date_of_birth": "1990-01-01",
                "gender": "Male",
                "nationality": "Angola",
                "status": "pending",
            }
        ]

        await monitor.start(applicants, auto_book=True)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await browser.stop()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="VFS Booking Bot")
    parser.add_argument(
        "command",
        choices=["api", "bot", "init"],
        help="Command to run: api (start API server), bot (run bot standalone), init (initialize database)",
    )

    args = parser.parse_args()

    if args.command == "api":
        run_api()
    elif args.command == "bot":
        asyncio.run(run_bot())
    elif args.command == "init":
        asyncio.run(init())
        logger.info("Initialization complete!")


if __name__ == "__main__":
    main()
