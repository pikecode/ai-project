"""Weather tool using wttr.in (free, no API key)."""

from typing import Any

import httpx

from .base import BaseTool


class WeatherTool(BaseTool):
    """Get weather information for a location."""

    name = "weather"
    description = "Get current weather for a city. Input: city name (e.g., 'Beijing', 'Shanghai')."
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name (e.g., 'Beijing', 'Tokyo', 'New York')",
            }
        },
        "required": ["city"],
    }

    async def execute(self, city: str) -> dict[str, Any]:
        """Get weather for city."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"https://wttr.in/{city}?format=j1"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

                current = data.get("current_condition", [{}])[0]
                weather = data.get("weather", [{}])[0]

                return {
                    "success": True,
                    "city": city,
                    "temperature": current.get("temp_C", "N/A") + "°C",
                    "condition": current.get("weatherDesc", [{}])[0].get("value", "N/A"),
                    "humidity": current.get("humidity", "N/A") + "%",
                    "wind": current.get("windspeedKmph", "N/A") + " km/h",
                    "forecast": (
                        [
                            {
                                "day": day.get("date", ""),
                                "max_temp": day.get("maxTempC", ""),
                                "min_temp": day.get("minTempC", ""),
                            }
                            for day in weather.get("weather", [])[:3]
                        ]
                        if weather
                        else []
                    ),
                }
        except Exception as e:
            return {"success": False, "error": str(e)}


def init_weather_tool():
    """Register weather tool."""
    from .base import registry

    registry.register(WeatherTool())
