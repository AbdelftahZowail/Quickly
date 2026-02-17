# Settings Migration - Environment to Database

## Summary
All configuration settings have been migrated from `.env` files to database storage. Users can now manage all settings from the frontend interface at `/settings`.

## What Changed

### Files Modified:
- **app/config.py** → **app/settings_manager.py** (replaced)
  - Settings now stored in database instead of reading from .env
  - Settings loaded at startup and cached in memory
  - Provides same interface (`settings.database_url`, `settings.test_mode`, etc.)

- **app/database.py**
  - Now imports from `settings_manager` instead of `config`
  - Calls `initialize_settings()` during database initialization

- **app/app_settings.py**
  - Updated to work with new settings system
  - Added `get_test_mode()` and `set_test_mode()` helpers
  - Auto-reloads settings cache when credentials are updated

- **app/routers/settings.py**
  - Added comprehensive API endpoints for all settings
  - GET `/api/settings/` - view all settings (sensitive values masked)
  - PUT `/api/settings/` - update all settings
  - GET/POST `/api/settings/test-mode` - toggle test mode
  - Existing Google OAuth endpoints remain

- **All files using settings** updated:
  - `app/main.py`
  - `app/sender.py`
  - `app/jobs.py`
  - `app/routers/test_mode.py`
  - `app/routers/gmail_oauth.py`

### New Files:
- **app/settings_manager.py** - Core settings management module
- **templates/settings.html** - Frontend UI for editing settings

### Frontend Changes:
- Added "Settings" link to main navigation
- New settings page with sections for:
  - General settings (base URL, queue interval, test mode)
  - Email provider selection (Resend/SMTP/Gmail)
  - Provider-specific configuration
  - Sensitive values are masked when displayed

## How It Works

1. **At Startup:** Database is initialized, then settings are loaded from `app_setting` table into memory
2. **During Runtime:** Code accesses `from app.settings_manager import settings` synchronously
3. **When Updated:** Settings API saves to database AND reloads memory cache
4. **First Run:** If no settings in DB, defaults are saved automatically

## Configuration

### Before (old .env approach):
```env
DATABASE_URL=sqlite+aiosqlite:///./campaign.db
BASE_URL=http://localhost:8000
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_xxxxx
TEST_MODE=true
```

### After (database + frontend):
1. Settings stored in `app_setting` table (key-value pairs)
2. Edit via web UI at http://localhost:8000/settings
3. Changes take effect immediately (no restart needed)

## Migration Path

### First Startup After Upgrade:
1. Application detects empty settings in database
2. Creates default settings automatically
3. Settings can be configured via frontend UI

### If You Have .env Settings:
The old `.env` file is no longer used. To preserve your configuration:
1. Start the application (defaults will be created)
2. Go to http://localhost:8000/settings
3. Enter your configuration values from `.env`
4. Click "Save Settings"

## Database Table

Settings are stored in the `app_setting` table:
```sql
CREATE TABLE app_setting (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME
);
```

Example rows:
- `base_url` → `http://localhost:8000`
- `email_provider` → `resend`
- `test_mode` → `true`

## Benefits

✅ **User-friendly:** Edit settings from web interface, no file system access needed  
✅ **No restarts:** Changes apply immediately  
✅ **Secure:** Sensitive values never exposed in frontend (masked display)  
✅ **Persistent:** Settings survive container rebuilds  
✅ **Validated:** Form validation ensures correct values  

## Notes

- The old `app/config.py` using Pydantic `BaseSettings` is replaced
- `DATABASE_URL` can still be set via environment variable for initial connection, then stored in DB
- All existing code continues to work with same `settings.xxx` interface
- Settings page accessible at `/settings` in the navigation menu
