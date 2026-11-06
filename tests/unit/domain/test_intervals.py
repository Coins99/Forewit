"""Half-open UTC intervals: unions, clipping and lengths."""

from datetime import UTC, datetime, timedelta

from mailbrief.domain.intervals import clip, minutes, overlaps, subtract, total_minutes, union

T = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def at(minute: int) -> datetime:
    return T + timedelta(minutes=minute)


def test_union_merges_overlapping_and_touching_spans_and_drops_empty_ones() -> None:
    spans = [(at(60), at(90)), (at(0), at(30)), (at(20), at(40)), (at(40), at(50)), (at(5), at(5))]
    assert union(spans) == ((at(0), at(50)), (at(60), at(90)))
    assert union([]) == ()


def test_clip_keeps_the_parts_inside_the_range_in_order() -> None:
    spans = [(at(-30), at(10)), (at(50), at(70)), (at(100), at(120)), (at(20), at(30))]
    assert clip(spans, at(0), at(60)) == ((at(0), at(10)), (at(50), at(60)), (at(20), at(30)))


def test_unique_minutes_count_overlaps_once_and_totals_count_each() -> None:
    spans = [(at(0), at(60)), (at(30), at(90))]
    assert minutes(spans) == 90
    assert total_minutes(spans) == 120
    assert total_minutes([(at(10), at(0))]) == 0


def test_touching_ends_do_not_overlap() -> None:
    assert overlaps((at(0), at(60)), (at(59), at(61)))
    assert not overlaps((at(0), at(60)), (at(60), at(61)))
    assert not overlaps((at(60), at(61)), (at(0), at(60)))


def test_subtract_takes_the_cuts_out_once_each() -> None:
    working = [(at(0), at(120)), (at(180), at(240))]
    cuts = [(at(30), at(60)), (at(50), at(70)), (at(110), at(190)), (at(300), at(400))]
    assert subtract(working, cuts) == ((at(0), at(30)), (at(70), at(110)), (at(190), at(240)))
    assert subtract(working, []) == ((at(0), at(120)), (at(180), at(240)))
    assert subtract(working, [(at(-10), at(500))]) == ()
    assert subtract([], cuts) == ()


def test_subtract_merges_its_spans_and_keeps_edges_that_only_touch() -> None:
    assert subtract([(at(30), at(60)), (at(0), at(40))], [(at(60), at(90))]) == ((at(0), at(60)),)
    assert subtract([(at(0), at(60))], [(at(0), at(10)), (at(50), at(60))]) == ((at(10), at(50)),)
