"""NAS search words: camelCase labels and Chinese place names (2026-10-31 plan, W5.1 and W5.6).

Generic fixtures only: the operator's index is personal data, so no real trip
name, clip label or itinerary appears here.
"""

from __future__ import annotations
from typing import Any, Dict, List

from reachy_companion.hanova import nas


def _folders(index: Dict[str, Any], **filters: Any) -> List[str]:
    return sorted({video["top_folder"] for video in nas.filter_index(index, **filters)})


CAMEL = {
    "videos": [
        {"top_folder": "2010 Trip A", "place": "Big City", "label": "OpeningNY", "year": 2010},
        {"top_folder": "2011 Trip B", "place": "Coast", "label": "NYTrip", "year": 2011},
        {"top_folder": "2012 Trip C", "place": "Desert", "label": "Sunny day", "year": 2012},
        {"top_folder": "2013 Trip D", "place": "Prairie", "label": "Road", "country": "USA", "year": 2013},
        {"top_folder": "2014 Trip E", "place": "Island", "label": "Canyon", "year": 2014},
    ]
}


def test_a_camelcase_label_splits_at_its_case_seams():
    """1.35.0 regression: "NY" stopped matching a label like "OpeningNY"."""
    assert _folders(CAMEL, keyword="NY") == ["2010 Trip A", "2011 Trip B"]
    assert _folders(CAMEL, keyword="ny") == ["2010 Trip A", "2011 Trip B"]
    assert _folders(CAMEL, keyword="opening") == ["2010 Trip A"]
    assert _folders(CAMEL, keyword="trip b") == ["2011 Trip B"]


def test_an_all_caps_word_stays_whole():
    """"USA" is one token; neither "US" nor "SA" is a word in it."""
    assert _folders(CAMEL, keyword="usa") == ["2013 Trip D"]
    assert _folders(CAMEL, keyword="us") == []
    assert _folders(CAMEL, keyword="sa") == []


def test_a_short_word_still_needs_a_whole_token():
    """The 1.35.0 rule stands: "ny" is not a piece of "Sunny" or "Canyon"."""
    assert "2012 Trip C" not in _folders(CAMEL, keyword="ny")
    assert "2014 Trip E" not in _folders(CAMEL, keyword="ny")


ZH = {
    "folders": {
        "2019 Trip K": {"year": 2019, "place": "Kingdom", "country": "Kingdomland", "place_zh": "甲國 甲国 乙城"},
        "2025 Trip K": {"year": 2025, "place": "Kingdom", "country": "Kingdomland", "place_zh": "甲國 甲国 乙城"},
        "2025 Trip M": {"year": 2025, "place": "Mountains", "country": "Mountland", "place_zh": "丙山 丁"},
        "2024 Trip N": {"year": 2024, "place": "North", "country": "Northland"},
    },
    "videos": [
        {"top_folder": "2019 Trip K", "place": "Kingdom", "country": "Kingdomland", "place_zh": "甲國 甲国 乙城",
         "label": "part 1", "year": 2019},
        {"top_folder": "2025 Trip K", "place": "Kingdom", "country": "Kingdomland", "place_zh": "甲國 甲国 乙城",
         "label": "part 1", "year": 2025},
        # The folder map carries `place_zh`; this video does not, and the folder's serves.
        {"top_folder": "2025 Trip M", "place": "Mountains", "country": "Mountland", "label": "part 1", "year": 2025},
        {"top_folder": "2024 Trip N", "place": "North", "country": "Northland", "label": "part 1", "year": 2024},
    ],
}


def test_a_chinese_place_name_finds_its_folder():
    """A bare Chinese name, in either the traditional or the simplified form listed."""
    assert _folders(ZH, place="甲國") == ["2019 Trip K", "2025 Trip K"]
    assert _folders(ZH, place="甲国") == ["2019 Trip K", "2025 Trip K"]
    assert _folders(ZH, keyword="乙城") == ["2019 Trip K", "2025 Trip K"]


def test_a_chinese_name_inside_a_phrase_finds_its_folder():
    """Mini passes phrases: 「甲國旅行」 holds 「甲國」."""
    assert _folders(ZH, place="甲國旅行") == ["2019 Trip K", "2025 Trip K"]
    assert _folders(ZH, keyword="去乙城玩的影片") == ["2019 Trip K", "2025 Trip K"]


def test_the_latin_words_beside_a_chinese_name_still_have_to_match():
    """「2025 甲國」 asks for the year too, as "2025 Kingdom" would."""
    assert _folders(ZH, keyword="2025 甲國") == ["2025 Trip K"]
    assert _folders(ZH, place="2025 甲國") == ["2025 Trip K"]
    assert _folders(ZH, keyword="1999 甲國") == []


def test_the_folder_map_place_zh_serves_a_video_without_one():
    """The builder writes both places; either one is enough."""
    assert _folders(ZH, place="丙山") == ["2025 Trip M"]


def test_a_one_character_place_zh_term_is_ignored():
    """「丁」 alone would match far too much, so it counts for nothing."""
    assert _folders(ZH, place="丁") == []
    assert _folders(ZH, keyword="丁丁的影片") == []


def test_without_place_zh_chinese_text_is_matched_as_before():
    """No `place_zh`: a Chinese word must occur in the record, so it misses an English one."""
    assert _folders(ZH, place="北方") == []
    plain = {"videos": [{"top_folder": "2020 家族旅行", "place": "Home", "label": "x", "year": 2020}]}
    assert _folders(plain, keyword="家族旅行") == ["2020 家族旅行"]
    assert _folders(plain, keyword="甲國") == []
