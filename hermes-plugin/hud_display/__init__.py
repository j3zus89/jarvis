"""Jarvis HUD display plugin - registration."""
from . import schemas, tools


def register(ctx):
    ctx.register_tool(name="hud_display", toolset="hud",
                      schema=schemas.HUD_DISPLAY, handler=tools.hud_display)
    ctx.register_tool(name="hud_dismiss", toolset="hud",
                      schema=schemas.HUD_DISMISS, handler=tools.hud_dismiss)
    ctx.register_tool(name="youtube_play", toolset="hud",
                      schema=schemas.YOUTUBE_PLAY, handler=tools.youtube_play)
