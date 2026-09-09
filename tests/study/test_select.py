from datetime import datetime
from zoneinfo import ZoneInfo

from bot.knowledge.models import Source
from bot.study.select import select_sources

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 3, 14, 0, tzinfo=NY)


def S(title, topics, body="x", days=0):
    return Source(
        path=f"sources/cs101/{title}.md",
        title=title,
        course="cs101",
        topics=topics,
        body=body,
        timestamp=NOW.replace(day=3 - days),
    )


def test_matches_on_title_and_topics_then_recency():
    old = S("Pointers old", ["pointers"], days=2)
    new = S("Pointers new", ["pointers"], days=0)
    other = S("Sorting", ["quicksort"])
    picked = select_sources("how do pointers work?", [other, old, new], 1000)
    assert [s.title for s in picked] == ["Pointers new", "Pointers old", "Sorting"]


def test_short_question_words_are_ignored():
    hit = S("The big heap", ["heap"])
    miss = S("Sorting", ["quicksort"])
    # "the" and "is" are under 4 characters, so only "heap" scores
    assert select_sources("is the heap ok", [miss, hit], 1000)[0].title == "The big heap"


def test_budget_stops_the_list_but_always_keeps_the_top_one():
    first = S("Pointers", ["pointers"], body="a" * 80)
    second = S("Sorting", ["sorting"], body="b" * 80)
    assert [s.title for s in select_sources("pointers", [first, second], 100)] == ["Pointers"]

    picked = select_sources("pointers", [first, second], 200)
    assert [s.title for s in picked] == ["Pointers", "Sorting"]


def test_the_top_source_is_truncated_to_the_budget_without_touching_the_original():
    big = S("Pointers", ["pointers"], body="a" * 500)
    picked = select_sources("pointers", [big], 100)
    assert len(picked[0].body) == 100 and len(big.body) == 500


def test_no_sources():
    assert select_sources("anything", [], 1000) == []
