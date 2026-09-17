from bot.agent.tools import TOOL_SCHEMAS, validate_call, PROFILE_SETTABLE

def test_schemas_cover_all_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"add_todo","update_todo","move_todo","add_event","update_event","delete_event",
                     "add_goal","update_goal","set_profile","snooze","reply","study","move_source"}

def test_validate_ok_and_errors():
    assert validate_call("add_todo", {"title": "x", "priority": 1}) == []
    assert validate_call("nope", {}) == ["unknown tool nope"]
    assert any("title" in e for e in validate_call("add_todo", {"priority": 1}))
    assert any("priority" in e for e in validate_call("add_todo", {"title": "x", "priority": 9}))
    assert any("start" in e for e in validate_call("add_event", {"title": "x", "start": "tomorrow"}))
    assert any("field" in e for e in validate_call("set_profile", {"field": "home_latlng", "value": "x"}))
    assert "morning_briefing" in PROFILE_SETTABLE and "home_latlng" not in PROFILE_SETTABLE
