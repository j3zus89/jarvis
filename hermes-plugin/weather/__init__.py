from . import schemas, tools


def register(ctx):
    ctx.register_tool(name="weather", toolset="weather",
                      schema=schemas.WEATHER, handler=tools.weather)
