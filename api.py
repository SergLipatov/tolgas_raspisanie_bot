# api.py
from fastapi import FastAPI, HTTPException, Query, Depends
from typing import List, Optional
import sqlite3
import os
import sys
from datetime import datetime, timedelta
import json

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import the database module from the bot
from database import Database
from timetable_parser import parse_timetable, get_groups_data

app = FastAPI(title="Timetable API", description="API for timetable bot")

# Database dependency
def get_db():
    db = Database()
    try:
        yield db
    finally:
        db.close()

# API endpoints
@app.get("/groups/")
def get_groups(db: Database = Depends(get_db), search: Optional[str] = None):
    if search:
        groups = db.search_groups_by_name(search)
    else:
        groups = db.get_all_groups()
    return {"groups": groups}

@app.post("/groups/update")
async def update_groups(db: Database = Depends(get_db)):
    try:
        groups = get_groups_data()

        if groups:
            count = 0
            for group in groups:
                if db.add_group(group['name'], group['value']):
                    count += 1

            return {"status": "success", "message": f"Added/updated {count} groups out of {len(groups)}"}
        else:
            raise HTTPException(status_code=500, detail="Failed to get groups list")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/timetable/{group_id}")
def get_timetable(
        group_id: int,
        date: Optional[str] = None,
        days: Optional[int] = 1,
        db: Database = Depends(get_db)
):
    try:
        if date is None:
            date = datetime.now().strftime('%d.%m.%Y')

        # If days > 1, get timetable for a period
        if days > 1:
            end_date = (datetime.strptime(date, '%d.%m.%Y') + timedelta(days=days-1)).strftime('%d.%m.%Y')
            lessons = db.get_timetable_for_period(group_id, date, end_date)

            # Group by date
            grouped_lessons = {}
            for lesson in lessons:
                if lesson[0] not in grouped_lessons:
                    grouped_lessons[lesson[0]] = []
                grouped_lessons[lesson[0]].append({
                    "number": lesson[1],
                    "time_start": lesson[2],
                    "time_end": lesson[3],
                    "subject": lesson[4],
                    "type": lesson[5],
                    "audience": lesson[6],
                    "teacher": lesson[7]
                })

            return {"group_id": group_id, "period": {"start": date, "end": end_date}, "lessons_by_date": grouped_lessons}
        else:
            # Get timetable for a single date
            lessons = db.get_timetable_for_group(group_id, date)

            return {
                "group_id": group_id,
                "date": date,
                "lessons": [
                    {
                        "date": lesson[0],
                        "number": lesson[1],
                        "time_start": lesson[2],
                        "time_end": lesson[3],
                        "subject": lesson[4],
                        "type": lesson[5],
                        "audience": lesson[6],
                        "teacher": lesson[7]
                    } for lesson in lessons
                ]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/timetable/{group_id}/update")
async def update_timetable(
        group_id: int,
        days: int = 30,
        db: Database = Depends(get_db)
):
    try:
        start_date = datetime.now().strftime('%d.%m.%Y')
        end_date = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')

        lessons = parse_timetable(group_id, start_date, end_date)

        if lessons:
            db.save_timetable(group_id, lessons)

            # Update next update time
            next_update = datetime.now() + timedelta(hours=24)
            db.save_update_info('timetable', group_id, next_update)

            return {"status": "success", "message": f"Updated {len(lessons)} lessons"}
        else:
            raise HTTPException(status_code=500, detail="Failed to get timetable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users/")
def get_users(db: Database = Depends(get_db), telegram_id: Optional[int] = None):
    try:
        conn = db.conn
        cursor = conn.cursor()

        if telegram_id:
            cursor.execute("SELECT id, telegram_id, username, first_name, last_name, registered_at FROM users WHERE telegram_id = ?", (telegram_id,))
        else:
            cursor.execute("SELECT id, telegram_id, username, first_name, last_name, registered_at FROM users")

        columns = [col[0] for col in cursor.description]
        users = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return {"users": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/subscriptions/")
def get_subscriptions(
        db: Database = Depends(get_db),
        telegram_id: Optional[int] = None
):
    try:
        if telegram_id:
            subscriptions = db.get_user_subscriptions(telegram_id)
            return {
                "subscriptions": [
                    {
                        "id": sub[0],
                        "group_name": sub[1],
                        "group_id": sub[2]
                    } for sub in subscriptions
                ]
            }
        else:
            conn = db.conn
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, u.telegram_id, u.username, g.name as group_name, g.group_id, s.notifications_enabled
                FROM subscriptions s
                JOIN users u ON s.user_id = u.id
                JOIN groups g ON s.group_id = g.group_id
            """)

            columns = [col[0] for col in cursor.description]
            subscriptions = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return {"subscriptions": subscriptions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/teachers/search/")
def search_teacher_schedule(
        name: str,
        db: Database = Depends(get_db)
):
    try:
        if len(name) < 3:
            raise HTTPException(status_code=400, detail="Search term must be at least 3 characters")

        lessons = db.find_teacher_lessons(name)

        # Group by teacher
        teachers = {}
        for lesson in lessons:
            date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

            if teacher not in teachers:
                teachers[teacher] = []

            teachers[teacher].append({
                "date": date,
                "number": number,
                "time_start": time_start,
                "time_end": time_end,
                "subject": subject,
                "type": lesson_type,
                "audience": audience,
                "group": group_name
            })

        return {"teachers": [{"name": name, "lessons": lessons} for name, lessons in teachers.items()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/rooms/search/")
def search_room_schedule(
        room_number: str,
        db: Database = Depends(get_db)
):
    try:
        lessons = db.find_room_lessons(room_number)

        # Group by room
        rooms = {}
        for lesson in lessons:
            date, number, time_start, time_end, subject, lesson_type, audience, teacher, group_name = lesson

            # Skip ЭИОС (online) lessons
            if "ЭИОС" in audience:
                continue

            if audience not in rooms:
                rooms[audience] = []

            rooms[audience].append({
                "date": date,
                "number": number,
                "time_start": time_start,
                "time_end": time_end,
                "subject": subject,
                "type": lesson_type,
                "teacher": teacher,
                "group": group_name
            })

        return {"rooms": [{"room": room, "lessons": lessons} for room, lessons in rooms.items()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.0", port=8000)
