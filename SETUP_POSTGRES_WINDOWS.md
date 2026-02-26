# PostgreSQL Setup on Windows for Quickly

This guide walks you through the steps needed to get the application running under
Windows by installing/starting a PostgreSQL server and pointing the app at it.

> These instructions assume you're working in the `C:\Users\Zowail\PythonProjects\Quickly`
> workspace and have already created a Python virtual environment (e.g. `.venv`).

---

## 1. Install or run PostgreSQL

You need a PostgreSQL server accessible on `localhost:5432` (default port).

### Option A – Install via the official Windows installer

1. Download the installer from the [PostgreSQL website](https://www.postgresql.org/download/windows/).
2. Run the installer and follow prompts. Set a password for the `postgres` superuser
   (e.g. `postgres` for local development).
3. Accept the default port (5432) unless you have a conflict.
4. When installation finishes, a service named `postgresql-x64-XX` will be running.

### Option B – Use Docker (no installation required)

If you prefer isolation, start a container:

```powershell
# pull image if needed and run
docker run --rm -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=quickly \
           -p 5432:5432 postgres:16
```

Leave this terminal open while developing. The container exposes Postgres on
`localhost:5432` with user `postgres` / password `postgres` and a database
named `quickly`.

---

## 2. Verify connectivity

Open a new PowerShell prompt (inside or outside the venv) and run:

```powershell
# use psql (installed with Postgres) or Docker exec if using container
psql -h localhost -U postgres -l
```

You should see a list of databases; `quickly` should appear. If you get a
connection error, double-check that the service/container is running and that the
port is reachable (firewall rules sometimes block 5432).

---

## 3. Configure the application

### a. Environment variable / .env file

The application reads `DATABASE_URL` from the environment at startup. If you
have a `.env` file at the project root it will be loaded automatically via
python-dotenv, so you can keep your values there instead of exporting them
every shell session.

Create a `.env` file containing the variables you need, for example (your
docker command binds host port 5433):

```dotenv
# database on localhost:5433 because of your container mapping
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/quickly

# optional values that are used when the app first starts; they are
# subsequently stored in the database and can be edited via the web UI
BASE_URL=http://localhost:8000
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
```

Rather than editing the file directly you can also set the vars at the
prompt:

```powershell
set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/quickly
set BASE_URL=http://localhost:8000
set GOOGLE_CLIENT_ID=...
set GOOGLE_CLIENT_SECRET=...
```

The important point is that the `DATABASE_URL` you supply should match the
port you exposed (5433 in your Docker run). Once the server has started the
values are written to the database and the UI settings page becomes the
authoritative source.

Alternatively, you can launch `uvicorn` with the variable in‑line:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost/quickly'
uvicorn app.main:app --reload
```

The first run will create the schema automatically and populate default settings
to the database.

### b. Web UI fallback
If you forget to set `DATABASE_URL`, the app will still start if a Postgres
instance is already reachable using the default value hard‑coded in the
`Settings` class (`postgresql+asyncpg://postgres:postgres@localhost/quickly`).
Otherwise you'll see the connection refused error during startup.

---

## 4. Start the server

With the environment variable in place and the database running, activate your
venv and run:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt    # only needed once
uvicorn app.main:app --reload
```

The console should show a startup sequence and indicate that the server is
listening on `http://127.0.0.1:8000`.

---

## 5. Additional notes

- If you want to use a different user/password/database, adjust the connection
  string accordingly.
- For testing, the `TEST_DATABASE_URL` environment variable is used; the
  default test suite uses an in‑memory SQLite database but you can point it at
  a real Postgres instance if desired.
- To edit settings after startup (including the `database_url`) visit
  `http://127.0.0.1:8000/settings` in your browser.

---

That’s it! You now have a working PostgreSQL backend for Quickly on Windows.
Feel free to refer back to this file when setting up a new development machine.