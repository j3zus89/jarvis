"""Jarvis JOB_AGENT plugin - registration."""
from . import schemas, tools


def register(ctx):
    ctx.register_tool(name="job_profile", toolset="job_search",
                      schema=schemas.JOB_PROFILE, handler=tools.job_profile)
    ctx.register_tool(name="job_match", toolset="job_search",
                      schema=schemas.JOB_MATCH, handler=tools.job_match)
