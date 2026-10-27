# Clinic Appointment Booking API

A REST API that lets clinics publish appointment slots and patients book or cancel them. Built with **Python, FastAPI, SQLite, and pytest**.

## Features

- Create, list, and delete appointment slots.
- Book and cancel appointments.
- Filter slots by clinic and availability.
- Prevent double-booking with SQLite write transactions (`BEGIN IMMEDIATE`) and a database-level partial unique index.
- Validate request data and return useful HTTP errors.
- Automated tests for booking rules and validation.
- Interactive API docs at `/docs`.

## Requirements

- Python 3.10+
- pip

## Run locally

### 1. Create and activate a virtual environment

**Windows PowerShell**
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Start the API

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive Swagger docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

The SQLite database is created automatically as `clinic.db` on startup. To use another path, set the `DATABASE_PATH` environment variable.

## Run tests

```bash
python -m pytest -v
```

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/slots` | Create a clinic slot |
| `GET` | `/slots` | List slots |
| `GET` | `/slots?available_only=true` | List only available slots |
| `GET` | `/slots?clinic_name=Central%20Clinic` | Filter by clinic |
| `DELETE` | `/slots/{slot_id}` | Delete an unbooked slot |
| `POST` | `/bookings` | Book a slot |
| `GET` | `/bookings/{booking_id}` | Get booking details |
| `DELETE` | `/bookings/{booking_id}` | Cancel a booking |

### Example: create a slot

```json
{
  "clinic_name": "Central Clinic",
  "starts_at": "2026-11-02T10:00:00+07:00",
  "ends_at": "2026-11-02T10:30:00+07:00"
}
```

Send to `POST /slots`. Datetimes must include a timezone offset.

### Example: book a slot

```json
{
  "slot_id": 1,
  "patient_name": "Asha Rao",
  "patient_email": "asha@example.com"
}
```

Send to `POST /bookings`.

## Booking correctness and concurrency

The booking endpoint opens a `BEGIN IMMEDIATE` SQLite transaction before checking whether the slot is available. SQLite serializes competing writers, so two requests cannot both successfully pass the availability check. A partial unique index on `bookings(slot_id)` for rows with `status = 'booked'` independently enforces one active booking per slot. A conflicting request receives HTTP `409 Conflict`.

Cancelling a booking changes its status to `cancelled`, so the slot becomes available again. A slot with an active booking cannot be deleted.

## Error behavior

- `404 Not Found`: slot or booking does not exist.
- `409 Conflict`: slot is already booked, duplicate slot, repeated cancellation, or deleting a slot with an active booking.
- `422 Unprocessable Entity`: invalid input, missing fields, malformed email, timezone-less datetime, or end time not after start time.

## Design notes

- SQLite is suitable for this small exercise and requires no separate database server.
- SQL values are parameterized.
- Booking correctness is enforced in both transaction logic and the database schema.
- Tests use a temporary database so they do not modify local development data.

## Future improvements

- Authentication and role-based access for clinics and patients.
- Pagination and structured logging.
- PostgreSQL deployment for a multi-instance production service.
- Rate limiting, migrations, and stronger email validation.
