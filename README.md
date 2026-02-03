# VFS Global Portugal Booking Bot

Automated visa appointment booking bot for VFS Global Portugal (Angola).

## Features

- **Automated Login**: Handles VFS Global login with Cloudflare Turnstile bypass
- **Slot Monitoring**: Continuously monitors for available appointment slots
- **Auto Booking**: Automatically books appointments when slots are found (2-5 seconds)
- **Dashboard**: Web-based admin panel for managing applicants
- **Notifications**: Telegram and Email alerts for booking updates
- **Session Persistence**: Maintains login sessions to reduce Turnstile challenges

## Requirements

- Python 3.10+
- 2Captcha account (for Turnstile solving)
- Telegram Bot (optional, for notifications)

## Installation

1. **Clone the repository**
   ```bash
   cd /home/admin/projects/reverse_bot
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate  # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers**
   ```bash
   playwright install chromium
   ```

5. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

6. **Initialize database**
   ```bash
   python run.py init
   ```

## Configuration

Edit `.env` file with your credentials:

```env
# VFS Global Credentials
VFS_EMAIL=your_email@example.com
VFS_PASSWORD=your_password

# 2Captcha API
CAPTCHA_API_KEY=your_2captcha_api_key

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Bot Settings
MONITOR_INTERVAL=30  # seconds between checks
HEADLESS=false  # set to true for headless mode
```

## Usage

### Start API Server (Dashboard)

```bash
python run.py api
```

Open http://localhost:8000 in your browser.

### Run Bot Standalone

```bash
python run.py bot
```

## API Endpoints

### Applicants
- `GET /api/applicants` - List all applicants
- `POST /api/applicants` - Create applicant
- `GET /api/applicants/{id}` - Get applicant
- `PUT /api/applicants/{id}` - Update applicant
- `DELETE /api/applicants/{id}` - Delete applicant

### Bookings
- `GET /api/bookings` - List all bookings
- `POST /api/bookings` - Create booking
- `GET /api/bookings/{id}` - Get booking details
- `DELETE /api/bookings/{id}` - Cancel booking

### Bot Control
- `GET /api/bot/status` - Get bot status
- `POST /api/bot/start` - Start bot
- `POST /api/bot/stop` - Stop bot

### Statistics
- `GET /api/stats` - Get overall statistics

## Project Structure

```
reverse_bot/
├── src/
│   ├── app/
│   │   ├── config.py      # Configuration
│   │   ├── database.py    # Database connection
│   │   ├── models.py      # SQLAlchemy models
│   │   ├── schemas.py     # Pydantic schemas
│   │   ├── crud.py        # CRUD operations
│   │   └── main.py        # FastAPI app
│   │
│   ├── automation/
│   │   ├── browser.py     # Browser management
│   │   ├── login.py       # Login automation
│   │   ├── turnstile.py   # Captcha solver
│   │   ├── booking.py     # Booking flow
│   │   └── monitor.py     # Slot monitoring
│   │
│   ├── services/
│   │   └── notification.py  # Telegram/Email
│   │
│   └── dashboard/
│       └── templates/
│           └── index.html  # Dashboard UI
│
├── data/
│   ├── vfs_bot.db         # SQLite database
│   ├── screenshots/       # Booking screenshots
│   └── logs/              # Log files
│
├── docs/                   # Documentation
├── requirements.txt
├── .env                    # Configuration
└── run.py                  # Entry point
```

## Operational Costs

| Service | Monthly Cost |
|---------|--------------|
| 2Captcha | $10-20 |
| **Total** | **$10-20/month** |

No proxy or server costs - runs directly on client PC.

## Troubleshooting

### Turnstile Not Solving
- Check 2Captcha API key is correct
- Check 2Captcha balance
- Try refreshing the page

### Login Failed
- Verify VFS credentials
- Check for account lockout
- Clear session data

### No Slots Found
- This is normal - slots are rare
- Reduce monitor interval if needed
- Ensure correct center/category

## Support

For issues, contact the developer.

---

**Developer:** Justin Gnoh
**Version:** 1.0.0
