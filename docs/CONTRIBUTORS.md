# Contributing to Quickly

Thanks for your interest in contributing! This guide will help you get set up and make meaningful contributions.

---

## Getting Started

### 1. Fork & Clone

```bash
git clone https://github.com/<your-username>/quickly.git
cd quickly
```

### 2. Set Up the Backend

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### 3. Set Up the Frontend

```bash
cd frontend
npm install
```

### 4. Start a Local PostgreSQL

Either install Postgres locally or use Docker:

```bash
docker run -d --name quickly-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=quickly \
  -p 5432:5432 \
  postgres:15-alpine
```

### 5. Run in Development Mode

**Backend:**

```bash
set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly
set QUICKLY_MODE=development
uvicorn app.main:app --reload
```

**Frontend (hot reload):**

```bash
cd frontend
npm run dev
```

The Vite dev server (`localhost:5173`) proxies API calls to the backend (`localhost:8000`).

---

## Project Structure

```
Quickly/
├── app/                    # FastAPI backend
│   ├── main.py             # App entry point, startup, routes
│   ├── models.py           # SQLAlchemy models
│   ├── schemas.py          # Pydantic schemas
│   ├── database.py         # Database engine & session
│   ├── jobs.py             # Background send job
│   ├── queue_logic.py      # Queue scheduling engine
│   ├── sender.py           # Email sending (Gmail OAuth only, bounce detection)
│   ├── tracking.py         # Open/click tracking logic
│   ├── unibox.py           # Gmail sync & unified inbox
│   ├── webhooks.py         # Outbound webhook firing
│   ├── settings_manager.py # Database-backed settings
│   └── routers/            # API route handlers
│       ├── campaigns.py
│       ├── leads.py
│       ├── inbox.py
│       ├── schedule.py
│       ├── settings.py
│       ├── gmail_oauth.py
│       ├── unibox.py
│       ├── tracking.py
│       └── test_mode.py
├── frontend/               # React + Vite + Tailwind
│   └── src/
│       ├── pages/          # Route pages
│       ├── components/     # Reusable UI components
│       ├── context/        # React context providers
│       └── api.js          # API client
├── tests/                  # pytest test suite
├── smoke_test/             # Development utilities
├── docs/                   # Documentation
├── docker-compose.yml      # Production Docker Compose
├── Dockerfile              # Multi-stage build
└── Caddyfile               # Caddy reverse proxy config
```

---

## Development Workflow

### Making Changes

1. Create a feature branch: `git checkout -b feature/my-change`
2. Make your changes
3. Run tests: `pytest`
4. Run the app and verify manually
5. Commit with a clear message
6. Open a pull request

### Running Tests

```bash
# Fast: uses in-memory SQLite
pytest

# Thorough: uses PostgreSQL (set env var first)
set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/test_quickly
pytest
```

Tests create and drop tables on every run, so always point at a disposable database.

### Code Style

- **Backend:** Follow PEP 8. Use type hints. Async functions for all database operations.
- **Frontend:** Functional components with hooks. Tailwind CSS for styling. No CSS modules.
- **Commits:** Use clear, descriptive commit messages. One logical change per commit.

---

## Architecture Notes

### Backend

- **FastAPI** with async SQLAlchemy (PostgreSQL via `asyncpg`)
- **APScheduler** runs the send job in-process (no separate worker)
- **No authentication** — the app is designed for personal/single-tenant use, secured at the network level
- Settings live in the database (`app_settings` table), loaded via `settings_manager.py`

### Frontend

- **React 18** with Vite for bundling
- **Tailwind CSS** for styling (utility-first, no component library)
- **React Router 6** for client-side routing (SPA with backend catch-all)
- **React Context API** for state (dark mode, notifications, loading, app mode)
- No Redux or external state management

### Queue Engine

The queue engine (`queue_logic.py`) is the heart of the scheduling system:

- When leads are added, all sequence slots are reserved immediately
- Slots are assigned to inboxes based on capacity (round-robin or priority)
- Business-day math respects sending days and hours per campaign
- Per-inbox daily limits and cooldown periods are enforced
- Changing wait days, adding/removing sequences, or modifying inboxes triggers automatic recalculation

---

## Areas for Contribution

- **Bug fixes** — Check open issues
- **Tests** — Improve coverage, especially for edge cases in queue logic
- **Documentation** — Improve guides, add examples
- **UI polish** — Accessibility, responsive design, animations
- **New features** — Discuss in an issue first before implementing large changes

---

## Reporting Issues

When filing a bug report, include:

1. Steps to reproduce
2. Expected vs. actual behaviour
3. Browser and OS
4. Docker/Python versions if relevant
5. Relevant logs (`docker compose logs app`)

---

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include tests for new functionality
- Update documentation if the change affects user-facing behaviour
- Make sure all existing tests pass before submitting
- Reference the related issue number in the PR description

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
