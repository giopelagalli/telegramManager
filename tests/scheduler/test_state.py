from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bot.scheduler.state import RuntimeState, Chain

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)

def test_round_trip(tmp_path):
    s = RuntimeState.load(tmp_path / "state.json")
    s.fired.add("morning:2026-09-03"); s.pause_until = NOW
    s.chain = Chain("checkin", NOW, NOW, 1, "dentist", ["hi"])
    s.proactive_sends.append(NOW)
    s.save(tmp_path / "state.json")
    t = RuntimeState.load(tmp_path / "state.json")
    assert t.fired == {"morning:2026-09-03"} and t.pause_until == NOW and t.chain.item == "dentist"
    assert t.proactive_sends == [NOW]

def test_prune_drops_old_day_keys_only():
    s = RuntimeState.load(__import__("pathlib").Path("/nonexistent/state.json"))
    s.fired |= {
        "morning:2026-08-30", "morning:2026-09-03", "checkin:2026-08-30:2",
        # event keys carry the file's creation date, not the day the job fires
        "leave:schedule/2026-08-01-x.md", "get_ready:schedule/2026-08-01-x.md",
        "missed:schedule/2026-08-01-x.md",
    }
    s.proactive_sends = [NOW - timedelta(hours=2), NOW - timedelta(minutes=10)]
    s.prune(NOW)
    assert s.fired == {
        "morning:2026-09-03",
        "leave:schedule/2026-08-01-x.md",
        "get_ready:schedule/2026-08-01-x.md",
        "missed:schedule/2026-08-01-x.md",
    }
    assert s.proactive_sends == [NOW - timedelta(minutes=10)]
