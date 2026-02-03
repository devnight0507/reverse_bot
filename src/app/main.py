"""
FastAPI Application - VFS Booking Bot API
"""
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
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

# Global bot state (in production, use Redis or database)
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


@app.get("/api/bot/status", response_model=schemas.BotStatusResponse)
async def get_bot_status():
    """Get current bot status"""
    return schemas.BotStatusResponse(**bot_state)


@app.post("/api/bot/start")
async def start_bot(
    request: schemas.BotStartRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session)
):
    """Start the booking bot"""
    if bot_state["is_running"]:
        raise HTTPException(status_code=400, detail="Bot is already running")

    # TODO: Implement bot start logic
    bot_state["is_running"] = True

    return {"message": "Bot started", "status": "running"}


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the booking bot"""
    if not bot_state["is_running"]:
        raise HTTPException(status_code=400, detail="Bot is not running")

    # TODO: Implement bot stop logic
    bot_state["is_running"] = False

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
