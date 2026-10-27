from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, field_validator

DATABASE_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "clinic.db"))


def connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db():
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_name TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (clinic_name, starts_at, ends_at)
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id INTEGER NOT NULL REFERENCES slots(id) ON DELETE RESTRICT,
            patient_name TEXT NOT NULL,
            patient_email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'booked'
                CHECK (status IN ('booked', 'cancelled')),
            created_at TEXT NOT NULL,
            cancelled_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_slot
        ON bookings(slot_id) WHERE status = 'booked';
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Clinic Appointment Booking API",
    description="A small REST API for publishing clinic time slots and booking appointments.",
    version="1.0.0",
    lifespan=lifespan,
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SlotCreate(BaseModel):
    clinic_name: str = Field(min_length=2, max_length=120)
    starts_at: datetime
    ends_at: datetime

    @field_validator("clinic_name")
    @classmethod
    def trim_clinic_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("clinic_name cannot be blank")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone, e.g. +07:00 or Z")
        return value


class BookingCreate(BaseModel):
    slot_id: int = Field(gt=0)
    patient_name: str = Field(min_length=2, max_length=120)
    patient_email: str = Field(min_length=5, max_length=254)

    @field_validator("patient_name", "patient_email")
    @classmethod
    def trim_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")
        return value

    @field_validator("patient_email")
    @classmethod
    def validate_email_shape(cls, value):
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("A valid email address is required")
        return value


def row_dict(row):
    return dict(row) if row is not None else None

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Clinic Appointment Booking API</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 850px;
                margin: 60px auto;
                padding: 20px;
                background: #f4f7fb;
                color: #243247;
            }
            h1 { color: #176b55; }
            .card {
                background: white;
                padding: 24px;
                border-radius: 12px;
                margin-top: 20px;
            }
            a { color: #176b55; }
            li { margin: 12px 0; }
        </style>
    </head>
    <body>
        <h1>Clinic Appointment Booking API</h1>
        <p>Welcome! The API is running successfully.</p>

        <div class="card">
            <h2>API Documentation</h2>
            <p><a href="/docs">Open Swagger API documentation</a></p>
            <p><a href="/health">Check API health</a></p>
        </div>

        <div class="card">
            <h2>Available Endpoints</h2>
            <ul>
                <li>POST /slots — Create a clinic slot</li>
                <li>GET /slots — List clinic slots</li>
                <li>DELETE /slots/{slot_id} — Delete a slot</li>
                <li>POST /bookings — Book an appointment</li>
                <li>GET /bookings/{booking_id} — View a booking</li>
                <li>DELETE /bookings/{booking_id} — Cancel a booking</li>
            </ul>
        </div>
    </body>
    </html>
    """
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/slots", status_code=status.HTTP_201_CREATED)
def create_slot(payload: SlotCreate):
    starts_at = payload.starts_at
    ends_at = payload.ends_at
    if ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    values = (
        payload.clinic_name,
        starts_at.isoformat(),
        ends_at.isoformat(),
        now_iso(),
    )
    with connect() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO slots (clinic_name, starts_at, ends_at, created_at) VALUES (?, ?, ?, ?)",
                values,
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="This clinic slot already exists",
            )
        row = conn.execute("SELECT * FROM slots WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return row_dict(row)


@app.get("/slots")
def list_slots(clinic_name: str | None = None, available_only: bool = False):
    sql = """
        SELECT s.*,
               CASE WHEN EXISTS (
                   SELECT 1 FROM bookings b
                   WHERE b.slot_id = s.id AND b.status = 'booked'
               ) THEN 0 ELSE 1 END AS available
        FROM slots s
    """
    clauses, params = [], []
    if clinic_name:
        clauses.append("s.clinic_name = ?")
        params.append(clinic_name.strip())
    if available_only:
        clauses.append("""NOT EXISTS (
            SELECT 1 FROM bookings b
            WHERE b.slot_id = s.id AND b.status = 'booked'
        )""")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY s.starts_at"
    with connect() as conn:
        return [row_dict(row) for row in conn.execute(sql, params).fetchall()]


@app.delete("/slots/{slot_id}")
def delete_slot(slot_id: int):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        slot = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
        if slot is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Slot not found")
        active = conn.execute(
            "SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'booked'",
            (slot_id,),
        ).fetchone()
        if active:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a slot with an active booking; cancel the booking first",
            )
        conn.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
        conn.commit()
    return {"message": "Slot deleted", "slot_id": slot_id}


@app.post("/bookings", status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate):
    with connect() as conn:
        # BEGIN IMMEDIATE serializes SQLite writers, so concurrent booking attempts
        # cannot both pass the availability check. The partial unique index is a
        # second line of defense at the database level.
        conn.execute("BEGIN IMMEDIATE")
        slot = conn.execute("SELECT * FROM slots WHERE id = ?", (payload.slot_id,)).fetchone()
        if slot is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Slot not found")
        existing = conn.execute(
            "SELECT id FROM bookings WHERE slot_id = ? AND status = 'booked'",
            (payload.slot_id,),
        ).fetchone()
        if existing:
            conn.rollback()
            raise HTTPException(status_code=409, detail="This slot is already booked")

        try:
            cursor = conn.execute(
                """INSERT INTO bookings
                   (slot_id, patient_name, patient_email, status, created_at)
                   VALUES (?, ?, ?, 'booked', ?)""",
                (payload.slot_id, payload.patient_name, payload.patient_email, now_iso()),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(status_code=409, detail="This slot is already booked")
        booking = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
        return row_dict(booking)


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        return row_dict(row)


@app.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: int):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Booking not found")
        if row["status"] == "cancelled":
            conn.rollback()
            raise HTTPException(status_code=409, detail="Booking is already cancelled")
        conn.execute(
            "UPDATE bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
            (now_iso(), booking_id),
        )
        updated = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()
        conn.commit()
        return row_dict(updated)
