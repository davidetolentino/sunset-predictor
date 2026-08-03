import sqlite3
from fastapi import FastAPI
from weather import get_sunset_time, get_weather_at_sunset
from pydantic import BaseModel, Field
from datetime import date, datetime

class RatingInput(BaseModel):
    rating: int = Field(ge=1, le=10)


app = FastAPI()

@app.get("/")
def home():
    return {"message": "Sunset Quality Predictor API"}

@app.get("/forecast")
def forecast():
    sunset_time = get_sunset_time()
    weather = get_weather_at_sunset(sunset_time)
    
    return {
        "sunset_time": sunset_time.strftime("%I:%M %p"),
        "cloud_cover_total": weather["cloud_cover"],
        "cloud_cover_low": weather["cloud_cover_low"],
        "cloud_cover_mid": weather["cloud_cover_mid"],
        "cloud_cover_high": weather["cloud_cover_high"],
        "humidity": weather["humidity"],
        "visibility": weather["visibility"],
        "temperature_f": round(weather["temperature"] * (9/5) + 32, 1),
        "wind_speed_kmh": weather["wind_speed"],
    }

@app.post("/rate")
def rate(data: RatingInput):
    sunset_time = get_sunset_time()
    weather = get_weather_at_sunset(sunset_time)
    
    conn = sqlite3.connect("sunset.db")
    cursor = conn.cursor()
    
    # NEW BLOCK 1: create table if it doesn't exist yet
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sunset_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            sunset_time TEXT,
            rating INTEGER NOT NULL,
            rating_source TEXT NOT NULL,
            cloud_cover REAL,
            cloud_cover_low REAL,
            cloud_cover_mid REAL,
            cloud_cover_high REAL,
            humidity REAL,
            visibility REAL,
            temperature REAL,
            wind_speed REAL,
            created_at TEXT NOT NULL
        )
    """)
    
    # NEW BLOCK 2: refuse duplicate rating for today
    cursor.execute(
        "SELECT id FROM sunset_ratings WHERE date = ?",
        (date.today().isoformat(),)
    )
    if cursor.fetchone():
        conn.close()
        return {"message": "Already rated today", "saved": False}
    
    # existing INSERT stays exactly as-is below
    cursor.execute("""
        INSERT INTO sunset_ratings 
            (date, sunset_time, rating, rating_source,
             cloud_cover, cloud_cover_low, cloud_cover_mid, 
             cloud_cover_high, humidity, visibility,
             temperature, wind_speed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date.today().isoformat(),
        sunset_time.isoformat(),
        data.rating,
        "in_person",
        weather["cloud_cover"],
        weather["cloud_cover_low"],
        weather["cloud_cover_mid"],
        weather["cloud_cover_high"],
        weather["humidity"],
        weather["visibility"],
        weather["temperature"],
        weather["wind_speed"],
        datetime.now().isoformat()
    ))
    
    conn.commit()
    conn.close()
    
    return {"message": "Rating saved", "rating": data.rating}



