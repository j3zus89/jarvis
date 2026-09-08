WEATHER = {
    "name": "weather",
    "description": (
        "Current conditions + today's/tomorrow's forecast for a location. "
        "Use for any weather question instead of web_search/web_extract — "
        "faster, same real data. Location may be a mangled voice-STT "
        "transcription; pass it as understood, this tool geocodes it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City/place name, e.g. 'Alsasua, Navarra, España'"},
            "day": {"type": "string", "enum": ["today", "tomorrow"], "description": "Defaults to today"},
        },
        "required": ["location"],
    },
}
