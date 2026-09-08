"""Tool schemas for the Jarvis HUD display plugin.

The description is what makes the agent USE the tool — keep it forceful.
Lesson learned: prose in SOUL.md cannot out-compete an attractive tool schema
(the model kept opening pages in its own invisible browser); a real tool with
an explicit description wins immediately.
"""

HUD_DISPLAY = {
    "name": "hud_display",
    "description": (
        "Show a video, webpage, or image ON THE USER'S SCREEN as a HUD "
        "panel. Use for 'show/display/put X on screen' — browser tools "
        "open pages invisibly, this is the only way the user actually "
        "sees it. YouTube watch/short URLs embed as playable video. Kanban: "
        "https://YOUR_HOST:9443/kanban, dashboard: https://YOUR_HOST:9443/ "
        "(media=iframe)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "media": {
                "type": "string",
                "enum": ["video", "iframe", "image"],
                "description": "video = YouTube or direct video URL; iframe = any webpage; image = image URL",
            },
            "src": {"type": "string", "description": "Full URL of the video, page, or image"},
            "title": {"type": "string", "description": "Short panel title, e.g. 'ARC REACTOR EXPLAINED'"},
            "position": {
                "type": "string",
                "enum": ["center", "left", "right"],
                "description": "center for one large panel; left/right for smaller side panels when showing multiple things",
            },
        },
        "required": ["media", "src", "title"],
    },
}

HUD_DISMISS = {
    "name": "hud_dismiss",
    "description": "Dismiss all holographic media panels from the user's Jarvis HUD screen. Use when they say to close, clear, or dismiss what's on screen.",
    "parameters": {"type": "object", "properties": {}},
}

YOUTUBE_PLAY = {
    "name": "youtube_play",
    "description": (
        "Search YouTube and play it on the user's HUD screen. Use for ANY "
        "'play/put on/watch X on YouTube' by title/artist/topic (not a "
        "full URL) — voice user, can't click links, so a URL in your reply "
        "plays nothing. Don't guess a video ID or use hud_display/browser "
        "for this, only this tool actually searches. If they say the title "
        "is coming, wait for it before calling."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for and play, e.g. 'Bohemian Rhapsody Queen' or 'lofi hip hop mix'",
            },
        },
        "required": ["query"],
    },
}
