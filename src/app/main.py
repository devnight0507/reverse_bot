"""
FastAPI Application - VFS Booking Bot API
"""
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import shutil
from loguru import logger

from .config import settings
from .database import init_db, get_session
from . import crud, schemas


# ============== Lifespan ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    # Startup
    logger.info("Starting VFS Booking Bot API...")

    # Ensure directories exist
    settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    logger.info("Shutting down VFS Booking Bot API...")


# ============== App Instance ==============

app = FastAPI(
    title="VFS Booking Bot API",
    description="API for VFS Global Portugal visa appointment booking automation",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files for dashboard
dashboard_path = Path(__file__).parent.parent / "dashboard"
if dashboard_path.exists():
    app.mount("/static", StaticFiles(directory=dashboard_path / "static"), name="static")


# ============== Dashboard Routes ==============

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve dashboard HTML"""
    index_path = dashboard_path / "templates" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>VFS Booking Bot API</h1><p>Dashboard not found. Visit /docs for API documentation.</p>")


# ============== Applicant Routes ==============

@app.post("/api/applicants", response_model=schemas.ApplicantResponse, status_code=status.HTTP_201_CREATED)
async def create_applicant(
    applicant: schemas.ApplicantCreate,
    db: AsyncSession = Depends(get_session)
):
    """Create a new applicant"""
    # Check for duplicate passport
    existing = await crud.get_applicant_by_passport(db, applicant.passport_number)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Applicant with passport {applicant.passport_number} already exists"
        )

    return await crud.create_applicant(db, **applicant.model_dump())


@app.get("/api/applicants", response_model=List[schemas.ApplicantResponse])
async def list_applicants(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_session)
):
    """List all applicants"""
    return await crud.get_applicants(db, skip=skip, limit=limit, status=status)


@app.get("/api/applicants/{applicant_id}", response_model=schemas.ApplicantResponse)
async def get_applicant(
    applicant_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Get applicant by ID"""
    applicant = await crud.get_applicant(db, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return applicant


@app.put("/api/applicants/{applicant_id}", response_model=schemas.ApplicantResponse)
async def update_applicant(
    applicant_id: int,
    applicant: schemas.ApplicantUpdate,
    db: AsyncSession = Depends(get_session)
):
    """Update an applicant"""
    existing = await crud.get_applicant(db, applicant_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Applicant not found")

    update_data = applicant.model_dump(exclude_unset=True)
    if not update_data:
        return existing

    return await crud.update_applicant(db, applicant_id, **update_data)


@app.delete("/api/applicants/{applicant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_applicant(
    applicant_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Delete an applicant"""
    deleted = await crud.delete_applicant(db, applicant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Applicant not found")


@app.post("/api/applicants/{applicant_id}/upload/{photo_type}")
async def upload_photo(
    applicant_id: int,
    photo_type: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session)
):
    """Upload face/passport photo for identity verification.
    photo_type: face_photo, passport_front, passport_page
    """
    applicant = await crud.get_applicant(db, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    if photo_type not in ("face_photo", "passport_front", "passport_page"):
        raise HTTPException(status_code=400, detail="Invalid photo type")

    # Save file
    uploads_dir = Path("data/uploads") / str(applicant_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".jpg"
    file_path = uploads_dir / f"{photo_type}{ext}"
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Update applicant record
    update_data = {f"{photo_type}_path": str(file_path)}
    await crud.update_applicant(db, applicant_id, **update_data)

    return {"message": f"{photo_type} uploaded", "path": str(file_path)}


# ============== Booking Routes ==============

@app.post("/api/bookings", response_model=schemas.BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    booking: schemas.BookingCreate,
    db: AsyncSession = Depends(get_session)
):
    """Create a new booking request"""
    # Check applicant exists
    applicant = await crud.get_applicant(db, booking.applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    return await crud.create_booking(db, **booking.model_dump())


@app.get("/api/bookings", response_model=List[schemas.BookingResponse])
async def list_bookings(
    skip: int = 0,
    limit: int = 100,
    applicant_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_session)
):
    """List all bookings"""
    return await crud.get_bookings(db, skip=skip, limit=limit, applicant_id=applicant_id, status=status)


@app.get("/api/bookings/{booking_id}", response_model=schemas.BookingResponse)
async def get_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Get booking by ID"""
    booking = await crud.get_booking(db, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


@app.delete("/api/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Cancel/delete a booking"""
    deleted = await crud.delete_booking(db, booking_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Booking not found")


@app.get("/api/bookings/{booking_id}/logs", response_model=List[schemas.BookingLogResponse])
async def get_booking_logs(
    booking_id: int,
    db: AsyncSession = Depends(get_session)
):
    """Get logs for a booking"""
    booking = await crud.get_booking(db, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return await crud.get_booking_logs(db, booking_id)


# ============== Statistics Routes ==============

@app.get("/api/stats", response_model=schemas.StatsResponse)
async def get_statistics(db: AsyncSession = Depends(get_session)):
    """Get overall statistics"""
    return await crud.get_statistics(db)


# ============== Bot Control Routes ==============

# Global bot state
bot_state = {
    "is_running": False,
    "current_applicant_id": None,
    "current_step": None,
    "total_processed": 0,
    "total_success": 0,
    "total_failed": 0,
    "last_check": None,
    "next_check": None,
}

# Global bot instances
_browser = None
_monitor = None


def _applicant_to_dict(a) -> dict:
    """Convert SQLAlchemy Applicant model to dict for monitor"""
    return {
        "id": a.id,
        "first_name": a.first_name,
        "last_name": a.last_name,
        "email": a.email,
        "phone": a.phone,
        "dial_code": a.dial_code,
        "passport_number": a.passport_number,
        "passport_expiry": a.passport_expiry,
        "date_of_birth": a.date_of_birth,
        "gender": a.gender,
        "nationality": a.nationality,
        "visa_type": a.visa_type,
        "face_photo_path": a.face_photo_path,
        "passport_front_path": a.passport_front_path,
        "passport_page_path": a.passport_page_path,
        "status": a.status,
    }


async def _run_bot(applicant_dicts: list):
    """Background task: run the slot monitor"""
    global _browser, _monitor
    from ..automation.browser import BrowserManager
    from ..automation.monitor import SlotMonitor

    try:
        _browser = BrowserManager()

        async def on_slot_found(event, data):
            bot_state["total_success"] += 1
            logger.info(f"Bot event: {event} - {data}")
            # Try to send Telegram notification
            try:
                from ..services.notification import NotificationService
                ns = NotificationService()
                if event == "slot_found":
                    await ns.notify_slot_found(data.get("message", "Slots available!"))
                elif event == "booking_success":
                    applicant = data.get("applicant", {})
                    confirmation = data.get("confirmation", {})
                    await ns.notify_booking_success(
                        f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}",
                        str(confirmation.get("appointment_date", "Unknown")),
                        confirmation_code=confirmation.get("appointment_ref"),
                    )
            except Exception as e:
                logger.error(f"Notification error: {e}")

        async def on_error(event, data):
            bot_state["total_failed"] += 1
            bot_state["current_step"] = f"Error: {event}"
            logger.error(f"Bot error: {event} - {data}")
            try:
                from ..services.notification import NotificationService
                ns = NotificationService()
                await ns.notify_error(str(data), event)
            except Exception:
                pass

        page = await _browser.start()
        if not page:
            logger.error("Failed to start browser")
            bot_state["is_running"] = False
            return

        bot_state["current_step"] = "Browser started"

        _monitor = SlotMonitor(_browser, on_slot_found=on_slot_found, on_error=on_error)

        # Determine visa category from first applicant
        visa_type = applicant_dicts[0].get("visa_type", "Visto Schengen") if applicant_dicts else "Visto Schengen"
        # Map visa_type to category/subcategory
        category_map = {
            "Visto Schengen": ("Visto Schengen", "Visto Schengen (Schengen Visa)"),
            "Visto Nacional": ("Visto Nacional", "Visto Nacional (National visa)"),
            "Job Seeker": ("Job Seeker", "Job seekers"),
        }
        category, subcategory = category_map.get(visa_type, ("Visto Schengen", "Visto Schengen (Schengen Visa)"))

        logger.info(f"Starting monitor for {len(applicant_dicts)} applicant(s), category: {category}")
        bot_state["current_step"] = "Monitoring"

        await _monitor.start(
            applicants=applicant_dicts,
            center="Luanda",
            category=category,
            subcategory=subcategory,
            auto_book=True,
        )

    except asyncio.CancelledError:
        logger.info("Bot task cancelled")
    except Exception as e:
        logger.error(f"Bot fatal error: {e}")
        bot_state["current_step"] = f"Fatal: {str(e)}"
    finally:
        bot_state["is_running"] = False
        bot_state["current_step"] = None
        bot_state["current_applicant_id"] = None
        if _browser:
            try:
                await _browser.stop()
            except Exception:
                pass
            _browser = None
        _monitor = None
        logger.info("Bot stopped")


# Keep reference to the background task so we can cancel it
_bot_task = None


@app.get("/api/bot/status", response_model=schemas.BotStatusResponse)
async def get_bot_status():
    """Get current bot status"""
    # Sync monitor stats if available
    if _monitor:
        stats = _monitor.stats
        bot_state["last_check"] = stats.get("last_check")
        bot_state["total_processed"] = stats.get("check_count", 0)
    return schemas.BotStatusResponse(**bot_state)


@app.post("/api/bot/start")
async def start_bot(
    request: schemas.BotStartRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session)
):
    """Start the booking bot for specified applicants"""
    global _bot_task

    if bot_state["is_running"]:
        raise HTTPException(status_code=400, detail="Bot is already running")

    # Load applicants from database
    if request.applicant_ids:
        applicants = []
        for aid in request.applicant_ids:
            a = await crud.get_applicant(db, aid)
            if a and a.status not in ("booked", "cancelled"):
                applicants.append(_applicant_to_dict(a))
        if not applicants:
            raise HTTPException(status_code=400, detail="No valid applicants found")
    else:
        # Load all pending applicants
        all_applicants = await crud.get_applicants(db, status="pending")
        applicants = [_applicant_to_dict(a) for a in all_applicants]
        if not applicants:
            raise HTTPException(status_code=400, detail="No pending applicants found")

    bot_state["is_running"] = True
    bot_state["current_applicant_id"] = applicants[0]["id"] if applicants else None
    bot_state["current_step"] = "Starting..."
    bot_state["total_processed"] = 0
    bot_state["total_success"] = 0
    bot_state["total_failed"] = 0

    # Launch bot as background asyncio task
    _bot_task = asyncio.create_task(_run_bot(applicants))

    names = ", ".join(a["first_name"] for a in applicants)
    return {"message": f"Bot started for: {names}", "status": "running"}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the booking bot"""
    global _bot_task

    if not bot_state["is_running"]:
        raise HTTPException(status_code=400, detail="Bot is not running")

    # Stop the monitor gracefully
    if _monitor:
        await _monitor.stop()

    # Cancel the background task if still running
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass
    _bot_task = None

    bot_state["is_running"] = False
    bot_state["current_step"] = None

    return {"message": "Bot stopped", "status": "stopped"}


# ============== Health Check ==============

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "1.0.0"}


# ============== Run Server ==============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
