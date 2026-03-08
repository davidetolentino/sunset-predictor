# backfill_ratings.py — Rate Historical Sunsets from Flickr Photos
# ================================================================
# This script lets you:
#   1. Enter a date and a sunset rating (from browsing Flickr photos)
#   2. Pulls ACTUAL historical weather data for that date from Open-Meteo
#   3. Saves the paired data (weather + your rating) to the database
#
# Usage:
#   python backfill_ratings.py
#
# It will prompt you to enter dates and ratings interactively.
# Type 'done' when you're finished entering ratings.
#
# WHY THIS MATTERS:
# Your ML model needs PAIRED data: weather conditions + human rating.
# The weather data can be backfilled from APIs, but ratings can only
# come from a human looking at a sunset (or a photo of one).
# This script bridges that gap by letting you rate historical photos
# and automatically pairing them with real weather observations.
#
# ARCHITECTURE NOTE:
# This script uses Open-Meteo's HISTORICAL API (archive-api.open-meteo.com)
# which is different from the forecast API in weather.py. The historical
# API returns actual observed/reanalysis data, not predictions. So the
# weather values here are what ACTUALLY happened, not what was forecasted.

import requests
import sqlite3
from datetime import datetime, date, timedelta
from astral import LocationInfo
from astral.sun import sun


# ============================================================
# CONFIGURATION — Same location as weather.py
# ============================================================
# In a larger project, you'd put these in a shared config file
# so weather.py and backfill_ratings.py both read from one place.
# For now, duplicating is fine. We'll refactor later.

LATITUDE = 37.7596
LONGITUDE = -122.4269
TIMEZONE = "America/Los_Angeles"
DB_NAME = "sunset.db"

LOCATION = LocationInfo(
    name="Dolores Park",
    region="USA",
    timezone=TIMEZONE,
    latitude=LATITUDE,
    longitude=LONGITUDE,
)


# ============================================================
# FUNCTION 1: Get sunset time for a specific date
# ============================================================
# Same as weather.py but accepts any date, not just today.
# This is why functions are powerful — we can reuse the logic
# with different inputs.

def get_sunset_time(target_date):
    """
    Calculate sunset time for Dolores Park on a given date.
    
    Args:
        target_date: a date object (e.g., date(2026, 1, 15))
    
    Returns:
        datetime object with the sunset time for that date
    """
    s = sun(LOCATION.observer, date=target_date, tzinfo=LOCATION.timezone)
    return s["sunset"]


# ============================================================
# FUNCTION 2: Pull HISTORICAL weather data from Open-Meteo
# ============================================================
# KEY DIFFERENCE from weather.py:
#   - weather.py uses api.open-meteo.com (forecast — predictions)
#   - this uses archive-api.open-meteo.com (historical — what actually happened)
#
# The historical API requires start_date and end_date parameters
# instead of just giving you the next 7 days automatically.

def get_historical_weather(target_date, sunset_time):
    """
    Fetch actual historical weather data from Open-Meteo for a
    specific date, and extract the hour closest to sunset.
    
    Args:
        target_date: date object for the day to look up
        sunset_time: datetime object for sunset on that date
    
    Returns:
        Dictionary with weather values at the sunset hour,
        or None if the API doesn't have data for that date
    """
    # Note the different base URL — this is the historical/archive API
    url = "https://archive-api.open-meteo.com/v1/archive"

    # Convert date to string format the API expects: "2026-01-15"
    date_str = target_date.isoformat()

    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": date_str,
        "end_date": date_str,       # Same day — we only need 24 hours
        "hourly": ",".join([
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "relative_humidity_2m",
            "visibility",
            "temperature_2m",
            "wind_speed_10m",
        ]),
        "timezone": TIMEZONE,
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print(f"  Warning: API returned status {response.status_code} for {date_str}")
        return None

    data = response.json()

    # Check if we got data back
    if "hourly" not in data or not data["hourly"]["time"]:
        print(f"  Warning: No historical data available for {date_str}")
        return None

    # Find the hour closest to sunset (same logic as weather.py)
    hourly_times = data["hourly"]["time"]
    best_index = 0
    smallest_diff = float("inf")

    for i, time_str in enumerate(hourly_times):
        hour_dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
        diff = abs((hour_dt - sunset_time.replace(tzinfo=None)).total_seconds())

        if diff < smallest_diff:
            smallest_diff = diff
            best_index = i

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
# FUNCTION 3: Create the ratings table + save a rating
# ============================================================
# NEW TABLE: sunset_ratings
# This is separate from weather_logs on purpose. weather_logs
# is your daily pipeline data. sunset_ratings is your ML training
# data — weather paired with human judgment.
#
# The "rating_source" column tracks whether the rating came from
# an in-person observation or a photo. This is important because:
#   - Photo ratings are noisier (one angle, one moment in time)
#   - In-person ratings capture the full experience
# Later, you might weight these differently during model training.

def save_rating(target_date, sunset_time, weather, rating, source="photo"):
    """
    Save a sunset rating paired with historical weather data.
    
    Args:
        target_date: date object
        sunset_time: datetime object
        weather: dictionary of weather values
        rating: integer 1-10
        source: "photo" or "in_person"
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Create the ratings table if it doesn't exist
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

    # Check if we already have a rating for this date
    # This prevents accidental duplicates (the problem you just hit!)
    cursor.execute(
        "SELECT id FROM sunset_ratings WHERE date = ?",
        (target_date.isoformat(),)
    )
    existing = cursor.fetchone()

    if existing:
        print(f"  Warning: You already have a rating for {target_date}. Skipping.")
        conn.close()
        return False

    # Insert the paired data
    cursor.execute("""
        INSERT INTO sunset_ratings
            (date, sunset_time, rating, rating_source, cloud_cover,
             cloud_cover_low, cloud_cover_mid, cloud_cover_high,
             humidity, visibility, temperature, wind_speed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        target_date.isoformat(),
        sunset_time.isoformat(),
        rating,
        source,
        weather["cloud_cover"],
        weather["cloud_cover_low"],
        weather["cloud_cover_mid"],
        weather["cloud_cover_high"],
        weather["humidity"],
        weather["visibility"],
        weather["temperature"],
        weather["wind_speed"],
        datetime.now().isoformat(),
    ))

    conn.commit()
    conn.close()
    return True


# ============================================================
# FUNCTION 4: Show current ratings in the database
# ============================================================

def show_ratings():
    """Display all ratings currently in the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Check if the table exists first
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='sunset_ratings'
    """)
    if not cursor.fetchone():
        print("  No ratings yet. Start entering some!")
        conn.close()
        return

    cursor.execute("""
        SELECT date, rating, rating_source, cloud_cover,
               cloud_cover_high, humidity
        FROM sunset_ratings
        ORDER BY date
    """)
    rows = cursor.fetchall()

    if not rows:
        print("  No ratings yet. Start entering some!")
    else:
        for row in rows:
            print(f"  {row[0]} | Rating: {row[1]}/10 ({row[2]}) | "
                  f"clouds: {row[3]}% | high clouds: {row[4]}% | humidity: {row[5]}%")
        print(f"\n  Total rated sunsets: {len(rows)}")

    conn.close()


# ============================================================
# MAIN BLOCK — Interactive rating entry
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  Sunset Backfill — Rate Historical Sunsets")
    print("=" * 55)
    print()
    print("Browse Flickr for SF sunset photos and rate them 1-10.")
    print("The script will automatically pull historical weather data.")
    print()
    print("Flickr search tip:")
    print("  https://www.flickr.com/search/?text=san+francisco+sunset")
    print("  Click into a photo to find the date it was taken.")
    print()
    print("Type 'done' when finished, 'show' to see your ratings.")
    print("-" * 55)
    print()

    today = date.today()

    while True:
        # Get date from user
        date_input = input("Enter date (YYYY-MM-DD) or 'done'/'show': ").strip().lower()

        if date_input == "done":
            print()
            print("Final summary:")
            show_ratings()
            print()
            print("Nice work! Each rated sunset is one training example")
            print("for your future ML model.")
            break

        if date_input == "show":
            print()
            show_ratings()
            print()
            continue

        # Parse and validate the date
        try:
            target_date = datetime.strptime(date_input, "%Y-%m-%d").date()
        except ValueError:
            print("  Invalid date format. Use YYYY-MM-DD (e.g., 2026-01-20)")
            print()
            continue

        # Check date is not in the future
        if target_date > today:
            print("  Can't rate a future sunset!")
            print()
            continue

        # Get rating from user
        try:
            rating = int(input(f"Your rating for {target_date} (1-10): ").strip())
            if rating < 1 or rating > 10:
                print("  Rating must be between 1 and 10.")
                print()
                continue
        except ValueError:
            print("  Please enter a number between 1 and 10.")
            print()
            continue

        # Ask if this was in-person or from a photo
        source_input = input("Was this in-person or from a photo? (p/i): ").strip().lower()
        if source_input == "i":
            source = "in_person"
        else:
            source = "photo"

        # Calculate sunset time for that date
        print(f"  Calculating sunset time for {target_date}...")
        sunset_time = get_sunset_time(target_date)
        print(f"  Sunset was at: {sunset_time.strftime('%I:%M %p')}")

        # Pull historical weather data
        print(f"  Fetching historical weather data...")
        weather = get_historical_weather(target_date, sunset_time)

        if weather is None:
            print("  Could not get weather data. Skipping this date.")
            print()
            continue

        # Show what we got
        temp_f = weather['temperature'] * (9/5) + 32
        print(f"  Weather at sunset:")
        print(f"    Clouds: {weather['cloud_cover']}% total | "
              f"low {weather['cloud_cover_low']}% | "
              f"mid {weather['cloud_cover_mid']}% | "
              f"high {weather['cloud_cover_high']}%")
        print(f"    Humidity: {weather['humidity']}% | "
              f"Temp: {temp_f:.1f}°F | "
              f"Wind: {weather['wind_speed']} km/h")

        # Save to database
        saved = save_rating(target_date, sunset_time, weather, rating, source=source)
        if saved:
            print(f"  Saved! Rating {rating}/10 for {target_date}")
        print()