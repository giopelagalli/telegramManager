from __future__ import annotations

from dataclasses import fields as _dc_fields
from datetime import date, datetime

from bot.knowledge.models import Profile

PROFILE_SETTABLE: frozenset[str] = frozenset(
    f.name for f in _dc_fields(Profile) if f.name not in ("home_latlng", "body", "places", "places_latlng")
)

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "Add a new todo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "integer", "enum": [1, 2, 3], "description": "1 high, 2 medium, 3 low"},
                    "due": {"type": "string", "format": "date"},
                    "due_time": {"type": "string", "description": "HH:MM 24h, when they gave a time (\"11:59pm\" → \"23:59\")"},
                    "goal": {"type": "string", "description": "path to a goal file"},
                    "backlog": {"type": "boolean"},
                    "verify": {"type": "string", "enum": ["none", "photo", "location", "question"]},
                    "notes": {"type": "string"},
                    "course": {"type": "string", "description": "course slug this is for, when it is class work"},
                },
                "required": ["title", "priority"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "Update an existing todo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "path to the todo file"},
                    "title": {"type": "string", "description": "new name, for a rename"},
                    "status": {"type": "string", "enum": ["open", "done", "dropped"]},
                    "priority": {"type": "integer", "enum": [1, 2, 3]},
                    "due": {"type": "string", "format": "date"},
                    "due_time": {"type": "string", "description": "HH:MM 24h, when they gave a time (\"11:59pm\" → \"23:59\")"},
                    "goal": {"type": "string"},
                    "verify": {"type": "string", "enum": ["none", "photo", "location", "question"]},
                    "notes": {"type": "string"},
                    "course": {"type": "string", "description": "course slug this is for, when it is class work"},
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_todo",
            "description": "Move a todo between todos/ and backlog/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "to": {"type": "string", "enum": ["todos", "backlog"]},
                },
                "required": ["file", "to"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": "Add a new schedule event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    "location": {"type": "string"},
                    "travel_minutes": {"type": "integer"},
                    "travel_mode": {"type": "string", "enum": ["walk", "drive"], "description": "how they get there, when they say"},
                    "prep_minutes": {"type": "integer"},
                    "importance": {"type": "string", "enum": ["normal", "critical"]},
                    "repeat_days": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]},
                        "description": "weekly recurrence, e.g. a class every Tue and Thu",
                    },
                    "repeat_until": {"type": "string", "format": "date"},
                    "kind": {"type": "string", "enum": ["exam", "quiz"], "description": "for tests"},
                    "course": {"type": "string", "description": "course slug from the context"},
                    "topics": {"type": "array", "items": {"type": "string"}, "description": "what the exam covers"},
                },
                "required": ["title", "start"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": "Update an existing schedule event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string", "format": "date-time"},
                    "end": {"type": "string", "format": "date-time"},
                    "location": {"type": "string"},
                    "travel_minutes": {"type": "integer"},
                    "travel_mode": {"type": "string", "enum": ["walk", "drive"], "description": "how they get there, when they say"},
                    "prep_minutes": {"type": "integer"},
                    "repeat_until": {"type": "string", "format": "date", "description": "last day of a weekly series (a class's semester end)"},
                    "importance": {"type": "string", "enum": ["normal", "critical"]},
                    "status": {"type": "string", "enum": ["upcoming", "left", "arrived", "done", "missed"]},
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete a schedule event.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string"}},
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_goal",
            "description": "Add a new goal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "period": {"type": "string", "description": '"2026", "2026-W36", or "2026-09"'},
                    "notes": {"type": "string"},
                },
                "required": ["title", "period"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_goal",
            "description": "Update an existing goal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "done", "dropped"]},
                    "notes": {"type": "string"},
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_profile",
            "description": "Set a profile field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "a settable profile field name"},
                    "value": {"description": "the new value"},
                },
                "required": ["field", "value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snooze",
            "description": 'Silence non-critical proactive messages, e.g. for "not now"/"stop"/"later".',
            "parameters": {
                "type": "object",
                "properties": {"minutes": {"type": "integer"}},
                "required": ["minutes"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "study",
            "description": "Answer from the user's stored course material (tutor). Use for questions "
            "about, or explanations of, anything in their courses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "course": {"type": "string", "description": "course slug from the context, if clear"},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_source",
            "description": "Move the most recently stored file to another course.",
            "parameters": {
                "type": "object",
                "properties": {"course": {"type": "string", "description": "course title or slug"}},
                "required": ["course"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_place",
            "description": "Remember a named place: \"my apartment is 123 Peachtree St\".",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "address": {"type": "string"}},
                "required": ["name", "address"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_base",
            "description": "Switch which saved place counts as home right now: \"I'm at the apartment "
            "this semester\", \"at my parents' this weekend\".",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directions",
            "description": "How far a place is and how to get there: \"directions to the gym\", \"how far is mags from me\". "
            "Resolves the place near the user, gives time and distance from where they are, and a Maps link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "the place as they said it"},
                    "mode": {"type": "string", "enum": ["drive", "walk"], "description": "walk when they're on foot or ask walking distance"},
                },
                "required": ["destination"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Look something up on the web when the answer needs current or outside "
            "information (facts, places, prices, news). Not for the user's own notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Remember something. kind=fact: durable (people, places, preferences, habits). "
            "kind=state: what's affecting their focus now (a distraction, a worry); fades after a month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "one sentence, second person"},
                    "kind": {"type": "string", "enum": ["fact", "state"]},
                },
                "required": ["fact"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search what the user said or did before (memories, change log, stored files).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coach",
            "description": "The user pasted a conversation with someone (or asks what to text them). "
            "Pass the thread and what they want.",
            "parameters": {
                "type": "object",
                "properties": {"thread": {"type": "string"}, "ask": {"type": "string"}},
                "required": ["thread"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo",
            "description": "Revert the last change: \"undo that\", \"no, put it back\".",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_food",
            "description": "They ate or drank something: log it with calories (and protein when you can). Exact from a "
            "menu/label when known; otherwise your best estimate from similar items and portions, with estimate=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "what and how much, e.g. 'Zaxby's Great 8 boneless meal'"},
                    "kcal": {"type": "integer"},
                    "protein_g": {"type": "integer"},
                    "carbs_g": {"type": "integer"},
                    "fat_g": {"type": "integer"},
                    "sodium_mg": {"type": "integer"},
                    "potassium_mg": {"type": "integer"},
                    "fiber_g": {"type": "integer"},
                    "sugar_g": {"type": "integer"},
                    "estimate": {"type": "boolean"},
                },
                "required": ["item", "kcal"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "briefing",
            "description": "Send the morning or evening briefing now, on request (with voice).",
            "parameters": {
                "type": "object",
                "properties": {"which": {"type": "string", "enum": ["morning", "evening"]}},
                "required": ["which"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply",
            "description": "The message to send back to the user. At most once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "boolean", "description": "true when they asked for a voice note / to hear it"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
]

# The tutor sees these instead of TOOL_SCHEMAS: in a course topic it either answers or files a note.
TUTOR_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save the message as a note source. Use it when the message is study "
            "content to keep, not a question to answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "the note, cleaned up"},
                    "topics": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
]

_TOOLS_BY_NAME: dict[str, dict] = {t["function"]["name"]: t["function"] for t in TOOL_SCHEMAS}

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def validate_call(name: str, args: dict) -> list[str]:
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return [f"unknown tool {name}"]

    schema = tool["parameters"]
    properties: dict = schema.get("properties", {})
    errors: list[str] = []

    for required_field in schema.get("required", []):
        if required_field not in args:
            errors.append(f"missing required field {required_field}")

    for key, value in args.items():
        prop = properties.get(key)
        if prop is None:
            errors.append(f"unknown field {key} for {name}")
            continue

        json_type = prop.get("type")
        if json_type is not None:
            expected = _JSON_TYPES.get(json_type)
            # bool is a subclass of int in Python; only accept it where the schema wants a boolean.
            if expected is not None and (
                not isinstance(value, expected) or (json_type != "boolean" and isinstance(value, bool))
            ):
                errors.append(f"{key} must be a {json_type}")
                continue

        enum = prop.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{key} must be one of {enum}, got {value!r}")

        fmt = prop.get("format")
        if fmt == "date":
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                errors.append(f"{key} must be an ISO date (YYYY-MM-DD)")
        elif fmt == "date-time":
            try:
                dt = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                errors.append(f"{key} must be an ISO datetime")
            else:
                if dt.tzinfo is None:
                    errors.append(f"{key} must include a UTC offset")

    if name == "set_profile" and "field" in args and args["field"] not in PROFILE_SETTABLE:
        errors.append(f"set_profile.field {args['field']!r} is not a settable profile field")

    return errors
