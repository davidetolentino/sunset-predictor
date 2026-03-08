# weather.py — Sunset Quality Predictor: Daily Weather Data Collector
# ====================================================================
# This script does three things:
#   1. Calculates today's exact sunset time for Dolores Park
#   2. Pulls weather data from Open-Meteo for the hour closest to sunset
#   3. Saves that snapshot to a SQLite database
#
# Run this daily to build your training dataset:
#   python weather.py
#
# WHY THESE FEATURES?
# Based on domain research (SunsetHue, sunset photography guides, and
# a Stanford paper on sunset prediction), the key predictors are:
#   - Cloud altitude: High clouds (cirrus) >> mid clouds >> low clouds
#     Low clouds block light. High clouds catch light for 30+ min after sunset.
#   - Cloud coverage: Partial coverage (30-70%) is ideal. 0% = boring, 100% = dark.
#   - Humidity: Lower humidity = more vibrant colors.
#   - Visibility: Clearer air = better light transmission.
#   - Wind: Affects cloud formation and movement.
#
# ARCHITECTURE NOTE:
# This script follows a common data pipeline pattern:
#   Collect (API) → Transform (find sunset hour) → Store (SQLite) → Verify
# Each step is its own function. This isn't just for neatness — when
# something breaks at 2am, you want to know WHICH step failed.
# This is the same pattern used in production ETL jobs at companies.

# ============================================================
# IMPORTS
# ============================================================
# requests — third-party library for HTTP calls (like httr in R)
# sqlite3  — built-in Python module for SQLite databases
#            (like DBI::dbConnect + DBI::dbWriteTable in R)
# datetime — built-in module for dates and times
# astral   — third-party library for astronomical calculations

import requests
import sqlite3
from datetime import datetime, date
from astral import LocationInfo
from astral.sun import sun


# ============================================================
# CONFIGURATION
# ============================================================
# ALL_CAPS = "these are constants, don't change at runtime"
# Keeping config at the top of the file is a professional pattern.
# Later, these could move to a config file or environment variables.

LATITUDE = 37.7596
LONGITUDE = -122.4269
TIMEZONE = "America/Los_Angeles"
DB_NAME = "sunset.db"

# LocationInfo object for astral's sunset calculation.
# This bundles our coordinates with timezone info so astral
# can compute astronomical events for this exact spot.
LOCATION = LocationInfo(
    name="Dolores Park",
    region="USA",
    timezone=TIMEZONE,
    latitude=LATITUDE,
    longitude=LONGITUDE,
)


# ============================================================
# FUNCTION 1: Get today's sunset time
# ============================================================
# Why a function? Same reason you'd write one in R — it isolates
# logic so you can reuse and test it independently.
# In R: get_sunset_time <- function() { ... }

def get_sunset_time():
    """
    Calculate today's sunset time for Dolores Park.
    
    Returns:
        datetime object with the exact sunset time
    
    The astral library uses astronomical formulas — the same math
    that powers weather apps and almanacs. The 'observer' property
    of our LOCATION gives astral the lat/lon it needs.
    """
    s = sun(LOCATION.observer, date=date.today(), tzinfo=LOCATION.timezone)
    return s["sunset"]


# ============================================================
# FUNCTION 2: Pull weather data from Open-Meteo
# ============================================================
# UPDATED: Now pulls cloud cover at THREE altitude levels instead
# of one combined number. This is the feature engineering insight
# from your SunsetHue research — knowing "60% clouds at high
# altitude" is way more predictive than just "60% clouds."
#
# Open-Meteo defines the levels as:
#   - cloud_cover_low:  below ~2,000m (6,500ft)  — often fog/marine layer
#   - cloud_cover_mid:  ~2,000m - 6,000m          — altocumulus, altostratus
#   - cloud_cover_high: above ~6,000m (20,000ft)  — cirrus, cirrostratus
#
# For SF specifically, cloud_cover_low often captures the marine
# layer / Karl the Fog, which is a sunset killer.

def get_weather_at_sunset(sunset_time):
    """
    Fetch weather data from Open-Meteo and extract the hour
    closest to today's sunset time.
    
    Args:
        sunset_time: datetime object for when sunset occurs
    
    Returns:
        Dictionary with weather values at the sunset hour
    
    In R terms:
        requests.get()  →  httr::GET()
        response.json() →  jsonlite::fromJSON()
        params dict      →  query = list(...) in httr
    """
    url = "https://api.open-meteo.com/v1/forecast"
    
    # NOTE: We request BOTH the combined cloud_cover AND the three
    # stratified levels. The combined one is useful as a quick summary;
    # the stratified ones are what your model will actually learn from.
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join([
            "cloud_cover",            # Total cloud cover (all levels combined)
            "cloud_cover_low",        # Low clouds: fog, marine layer, stratus
            "cloud_cover_mid",        # Mid clouds: altocumulus, altostratus
            "cloud_cover_high",       # High clouds: cirrus (the sunset stars!)
            "relative_humidity_2m",   # Humidity — high values mute colors
            "visibility",             # How far you can see — proxy for air clarity
            "temperature_2m",         # Temperature in Celsius
            "wind_speed_10m",         # Wind at 10m above ground
        ]),
        "timezone": TIMEZONE,
    }

    # Make the API call
    response = requests.get(url, params=params)

    # Basic error handling — don't save garbage data if API is down
    if response.status_code != 200:
        raise Exception(f"API request failed with status {response.status_code}")

    data = response.json()

    # --- Find the hour closest to sunset ---
    # The API returns 168 hourly timestamps (7 days x 24 hours).
    # We need to find which one is closest to our sunset time.
    #
    # Strategy: loop through timestamps, parse each one, find the
    # one with the smallest time difference from sunset.
    # This is like: which.min(abs(times - sunset)) in R.
    
    hourly_times = data["hourly"]["time"]
    best_index = 0
    smallest_diff = float("inf")  # Start with infinity so any real diff beats it

    for i, time_str in enumerate(hourly_times):
        # strptime = "string parse time" — converts "2026-02-15T17:00"
        # into a datetime object so we can do math with it.
        # In R this would be: as.POSIXct(time_str, format="%Y-%m-%dT%H:%M")
        hour_dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")

        # Only look at TODAY's hours (skip the other 6 forecast days)
        if hour_dt.date() != sunset_time.date():
            continue

        # How many seconds apart is this hour from sunset?
        # .total_seconds() converts a timedelta → plain number
        diff = abs((hour_dt - sunset_time.replace(tzinfo=None)).total_seconds())

        if diff < smallest_diff:
            smallest_diff = diff
            best_index = i

    # Pull values at the sunset-closest hour from each data array
    weather = {
        "time":             hourly_times[best_index],
        "cloud_cover":      data["hourly"]["cloud_cover"][best_index],
        "cloud_cover_low":  data["hourly"]["cloud_cover_low"][best_index],
        "cloud_cover_mid":  data["hourly"]["cloud_cover_mid"][best_index],
        "cloud_cover_high": data["hourly"]["cloud_cover_high"][best_index],
        "humidity":         data["hourly"]["relative_humidity_2m"][best_index],
        "visibility":       data["hourly"]["visibility"][best_index],
        "temperature":      data["hourly"]["temperature_2m"][best_index],
        "wind_speed":       data["hourly"]["wind_speed_10m"][best_index],
    }

    return weather


# ============================================================
# FUNCTION 3: Save to SQLite database
# ============================================================
# YOUR SQL KNOWLEDGE APPLIES DIRECTLY HERE.
# The only new thing is the Python "wrapper" around it.
#
# Pattern: connect → cursor → execute → commit → close
# Think of it like:
#   R: conn <- DBI::dbConnect(RSQLite::SQLite(), "sunset.db")
#   Python: conn = sqlite3.connect("sunset.db")
#
# WHERE DOES THE DATABASE LIVE?
# When you run sqlite3.connect("sunset.db"), Python creates (or opens)
# the file in your CURRENT WORKING DIRECTORY — meaning whatever folder
# your terminal is in when you run `python weather.py`. So if you're
# in C:\Users\ed4er\sunset-predictor, the file appears right there
# next to weather.py. It's just a regular file. No server needed.
# You can see it in VS Code's file explorer after the first run.
#
# PARAMETERIZED QUERIES (the ? placeholders):
# NEVER build SQL with f-strings like:
#   f"INSERT INTO ... VALUES ('{value}')"    ← SQL INJECTION RISK!
# ALWAYS use placeholders:
#   "INSERT INTO ... VALUES (?)", (value,)   ← SAFE
# The ? gets replaced by the value from the tuple, but SQLite
# handles escaping/sanitization. This is a non-negotiable habit
# for any code that touches a database.

def save_to_database(sunset_time, weather):
    """
    Save a weather snapshot to the SQLite database.
    
    Args:
        sunset_time: datetime object for sunset
        weather: dictionary from get_weather_at_sunset()
    
    The database file is created automatically on first run.
    CREATE TABLE IF NOT EXISTS means it won't crash on repeat runs.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Create table if this is the first run.
    # NOTE: We now have separate columns for low/mid/high clouds.
    # This schema directly reflects the domain knowledge from your
    # SunsetHue research — the model will be able to learn that
    # high clouds help and low clouds hurt sunset quality.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            sunset_time TEXT,
            cloud_cover REAL,
            cloud_cover_low REAL,
            cloud_cover_mid REAL,
            cloud_cover_high REAL,
            humidity REAL,
            visibility REAL,
            temperature REAL,
            wind_speed REAL
        )
    """)

    # Insert today's data using parameterized query
    cursor.execute("""
        INSERT INTO weather_logs 
            (timestamp, sunset_time, cloud_cover, cloud_cover_low,
             cloud_cover_mid, cloud_cover_high, humidity, visibility,
             temperature, wind_speed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),           # When you ran the script
        sunset_time.isoformat(),              # Today's calculated sunset time
        weather["cloud_cover"],               # Total cloud cover %
        weather["cloud_cover_low"],           # Low clouds % (fog/marine layer)
        weather["cloud_cover_mid"],           # Mid clouds % (altocumulus)
        weather["cloud_cover_high"],          # High clouds % (cirrus — the good ones!)
        weather["humidity"],                  # Relative humidity %
        weather["visibility"],               # Visibility in meters
        weather["temperature"],              # Temperature in Celsius
        weather["wind_speed"],               # Wind speed in km/h
    ))

    # Commit = save to disk. Without this, your INSERT only exists in memory.
    # This is like DBI::dbCommit(conn) in R, except in R with RSQLite
    # auto-commit is often on by default. In Python, it's explicit.
    conn.commit()
    conn.close()


# ============================================================
# MAIN BLOCK
# ============================================================
# "if __name__ == '__main__'" means: only run this code when
# executing the file directly (python weather.py).
#
# If another script does "from weather import get_weather_at_sunset",
# it can use the function WITHOUT running the whole pipeline.
# R doesn't have this pattern — scripts always run top to bottom.
# This is one of the biggest structural differences in Python.

if __name__ == "__main__":
    print("=" * 55)
    print("  Sunset Quality Predictor — Daily Data Collection")
    print("=" * 55)
    print()

    # Step 1: Calculate sunset time
    print("Step 1: Calculating today's sunset time...")
    sunset_time = get_sunset_time()
    print(f"  Sunset today: {sunset_time.strftime('%I:%M %p')}")
    print()

    # Step 2: Get weather data for the sunset hour
    print("Step 2: Fetching weather data from Open-Meteo...")
    weather = get_weather_at_sunset(sunset_time)
    print(f"  Closest hour:      {weather['time']}")
    print(f"  Cloud cover total: {weather['cloud_cover']}%")
    print(f"  Cloud cover low:   {weather['cloud_cover_low']}%  (fog/marine layer)")
    print(f"  Cloud cover mid:   {weather['cloud_cover_mid']}%  (altocumulus)")
    print(f"  Cloud cover high:  {weather['cloud_cover_high']}%  (cirrus — best for sunsets!)")
    print(f"  Humidity:          {weather['humidity']}%")
    print(f"  Visibility:        {weather['visibility']}m")
    temp_f = weather['temperature'] * (9/5) + 32
    print(f"  Temperature:       {temp_f:.1f}°F")
    print(f"  Wind speed:        {weather['wind_speed']} km/h")
    print()

    # Step 3: Save to database
    print("Step 3: Saving to database...")
    save_to_database(sunset_time, weather)
    print(f"  Saved to {DB_NAME}")
    print()

    # Step 4: Show database contents for verification
    # This is your "sanity check" — like running SELECT * after an INSERT
    print("Step 4: Current database contents:")
    print("-" * 55)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, timestamp, sunset_time, cloud_cover,
               cloud_cover_low, cloud_cover_mid, cloud_cover_high
        FROM weather_logs
        ORDER BY id
    """)
    rows = cursor.fetchall()
    for row in rows:
        print(f"  Row {row[0]}: {row[1][:10]} | sunset {row[2][:16]}")
        print(f"         clouds: {row[3]}% total | low {row[4]}% | mid {row[5]}% | high {row[6]}%")
    print(f"\n  Total records: {len(rows)}")
    conn.close()

    print()
    print("Done! Run again tomorrow to add another data point.")
    print("Pro tip: Look at the sky at sunset and mentally rate it 1-10.")
    print("         You'll be submitting real ratings in Sprint 2!")