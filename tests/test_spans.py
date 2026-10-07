from kgps.spans import parse_span


def test_parses_kgis_chunk_fragment():
    span = parse_span("doi:10.1/x#chunk:2@chars:180-240")
    assert span is not None
    assert (span.locator, span.chunk_index, span.start, span.end) == ("doi:10.1/x", 2, 180, 240)
    assert span.length == 60


def test_parses_bare_char_fragment():
    span = parse_span("file.txt#chars:0-10")
    assert span is not None and span.chunk_index is None


def test_locator_with_hash_in_it_uses_last_fragment():
    span = parse_span("https://x.org/a#b#chunk:0@chars:1-2")
    assert span is not None and span.locator == "https://x.org/a#b"


def test_non_span_fragments_are_none():
    assert parse_span("roster@snapshot=abc#player_id=7") is None
    assert parse_span("no-fragment") is None
    assert parse_span("#chars:1-2") is None
    assert parse_span("doc#chars:9-2") is None
