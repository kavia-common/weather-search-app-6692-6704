import os
from typing import Any, Dict

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"

openapi_tags = [
    {"name": "Health", "description": "Service health and readiness endpoints."},
    {"name": "Weather", "description": "Fetch current weather for a given city (OpenWeatherMap)."},
]

app = FastAPI(
    title="Weather Backend API",
    description=(
        "Backend API for the Weather Search App.\n\n"
        "Provides a `/weather` endpoint that proxies OpenWeatherMap Current Weather API and "
        "returns normalized fields for the frontend."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# Keep CORS enabled for the frontend on localhost:3000 (as requested).
# We include both localhost and 127.0.0.1 to reduce dev friction.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WeatherResponse(BaseModel):
    """Normalized weather response consumed by the frontend."""

    city: str = Field(..., description="City name used for the query (as returned by OpenWeatherMap).")
    temperature: float = Field(..., description="Current temperature in Celsius.")
    humidity: int = Field(..., description="Current humidity percentage.")
    description: str = Field(..., description="Short weather description (e.g., 'clear sky').")


class WeatherRequest(BaseModel):
    """POST request body for weather query."""

    city: str = Field(..., min_length=1, description="City name to lookup (e.g., 'London').")


def _normalize_openweather_payload(payload: Dict[str, Any]) -> WeatherResponse:
    """
    Convert OpenWeatherMap payload to our normalized schema.

    Raises:
        HTTPException: if payload doesn't contain expected fields.
    """
    try:
        city = payload["name"]
        temperature = float(payload["main"]["temp"])
        humidity = int(payload["main"]["humidity"])
        # weather is a list of conditions; pick the first
        description = str(payload["weather"][0]["description"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # Upstream format not as expected -> treat as upstream error.
        raise HTTPException(
            status_code=502,
            detail="Upstream response format from OpenWeatherMap was unexpected.",
        ) from exc

    return WeatherResponse(
        city=city,
        temperature=temperature,
        humidity=humidity,
        description=description,
    )


async def _fetch_current_weather(city: str, api_key: str) -> WeatherResponse:
    """
    Fetch current weather for a city using OpenWeatherMap.

    Error mapping rules (per requirements):
    - city not found -> 404
    - upstream/network errors -> 502
    """
    params = {"q": city, "appid": api_key, "units": "metric"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(OPENWEATHER_CURRENT_URL, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Failed to reach OpenWeatherMap.") from exc

    # OpenWeatherMap sometimes returns JSON with cod/message even on non-2xx.
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="City not found.")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"OpenWeatherMap returned an error (status {resp.status_code}).",
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="OpenWeatherMap returned invalid JSON.") from exc

    return _normalize_openweather_payload(payload)


# PUBLIC_INTERFACE
@app.get(
    "/",
    tags=["Health"],
    summary="Health Check",
    description="Simple health check endpoint.",
)
def health_check() -> Dict[str, str]:
    """Return a basic liveness response."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/weather",
    response_model=WeatherResponse,
    tags=["Weather"],
    summary="Get current weather by city (query param).",
    description=(
        "Fetch current weather for a city using OpenWeatherMap.\n\n"
        "Example: `GET /weather?city=London`"
    ),
    responses={
        400: {"description": "Missing/invalid city parameter."},
        404: {"description": "City not found."},
        500: {"description": "Server misconfiguration (missing API key)."},
        502: {"description": "Upstream/network error."},
    },
)
async def get_weather(city: str = Query(..., min_length=1, description="City name (e.g., 'London').")) -> WeatherResponse:
    """
    Get normalized current weather.

    Parameters:
        city: City name to look up.

    Returns:
        Normalized weather response: { city, temperature, humidity, description }.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENWEATHER_API_KEY is not configured on the server.",
        )

    # Query(..., min_length=1) already validates, but keep explicit guard for clarity.
    if not city or not city.strip():
        raise HTTPException(status_code=400, detail="Missing required parameter: city.")

    return await _fetch_current_weather(city.strip(), api_key)


# PUBLIC_INTERFACE
@app.post(
    "/weather",
    response_model=WeatherResponse,
    tags=["Weather"],
    summary="Get current weather by city (JSON body).",
    description=(
        "Fetch current weather for a city using OpenWeatherMap.\n\n"
        "Example body: `{ \"city\": \"London\" }`"
    ),
    responses={
        400: {"description": "Missing/invalid city in request body."},
        404: {"description": "City not found."},
        500: {"description": "Server misconfiguration (missing API key)."},
        502: {"description": "Upstream/network error."},
    },
)
async def post_weather(payload: WeatherRequest = Body(...)) -> WeatherResponse:
    """
    Get normalized current weather (POST variant).

    Parameters:
        payload: JSON body containing city.

    Returns:
        Normalized weather response: { city, temperature, humidity, description }.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENWEATHER_API_KEY is not configured on the server.",
        )

    city = (payload.city or "").strip()
    if not city:
        raise HTTPException(status_code=400, detail="Missing required field: city.")

    return await _fetch_current_weather(city, api_key)
