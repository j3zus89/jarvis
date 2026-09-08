"""Tool schemas for JOB_AGENT (docs/instrucciones herrameintas.md).

Two tools, deliberately small and single-purpose — this plugin does NOT
search the web itself. Hermes already has real web search/browsing; these
tools only supply the structured profile and do the compatibility scoring
in Python (zero extra model tokens), so Hermes doesn't have to guess
distances/salary math itself.
"""

JOB_PROFILE = {
    "name": "job_profile",
    "description": (
        "User's job-search profile: location, radius, desired roles, "
        "skills, salary min, transport, restrictions. Call FIRST for any "
        "job-search request, before web_search — needed for job_match's "
        "filtering even if the user already gave a role/location. Empty "
        "fields mean unset; ask the user rather than guessing."
    ),
    "parameters": {"type": "object", "properties": {}},
}

JOB_MATCH = {
    "name": "job_match",
    "description": (
        "Call before replying with job offers — do not paste raw "
        "web_search results or tell the user to search themselves. Scores, "
        "dedupes, and filters postings against the profile (real distance, "
        "salary, vehicle requirement) — math you can't do reliably "
        "yourself. Partial data ok, only title+url required per posting. "
        "A listing/category page (many jobs on one URL) is not a posting — "
        "extract individual postings first if that's all web_search gave. "
        "Returns ranked matches plus discards with reasons."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "postings": {
                "type": "array",
                "description": "Job postings found via web search, one object per posting.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Job title (required)"},
                        "url": {"type": "string", "description": "Link to the posting (required)"},
                        "company": {"type": "string"},
                        "location": {"type": "string", "description": "City/area as written in the posting"},
                        "salary": {"type": "string", "description": "Salary as written, if shown, e.g. '24.000-28.000 €'"},
                        "contract": {"type": "string", "description": "e.g. 'indefinido', 'temporal', 'freelance'"},
                        "schedule": {"type": "string", "description": "e.g. 'jornada completa', 'media jornada'"},
                        "description": {"type": "string", "description": "Short summary of the posting's content"},
                        "requirements": {"type": "string", "description": "Requirements text, if visible"},
                        "source": {"type": "string", "description": "e.g. 'LinkedIn', 'InfoJobs', 'Indeed'"},
                    },
                    "required": ["title", "url"],
                },
            },
        },
        "required": ["postings"],
    },
}
