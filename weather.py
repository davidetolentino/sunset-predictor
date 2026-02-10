import requests


from datetime import datetime

# Dolores Park coordinates
LATITUDE = 37.7596
LONGITUDE = -122.4269

# Build the API URL
url = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "hourly": "cloud_cover,relative_humidity_2m,visibility,temperature_2m,wind_speed_10m",
    "timezone": "America/Los_Angeles"
}

# Make the API call
print("Fetching weather data for Dolores Park...")
response = requests.get(url, params=params)
data = response.json()

# Print the raw response to see what we're working with
print(f"Status code: {response.status_code}")
print(f"Hours available: {len(data['hourly']['time'])}")
print()

# Let's look at the first few hours of data
for i in range(5):
    time = data["hourly"]["time"][i]
    clouds = data["hourly"]["cloud_cover"][i]
    humidity = data["hourly"]["relative_humidity_2m"][i]
    visibility = data["hourly"]["visibility"][i]
    temp = data["hourly"]["temperature_2m"][i]
    wind = data["hourly"]["wind_speed_10m"][i]

    print(f"Time: {time}")
    print(f"  Cloud cover: {clouds}%")
    print(f"  Humidity: {humidity}%")
    print(f"  Visibility: {visibility}m")
    print(f"  Temperature: {temp}°C")
    print(f"  Wind speed: {wind} km/h")
    print()

