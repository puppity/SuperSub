from subtitle_translator.core.models import Character, Cue, Episode
from subtitle_translator.core.series import (
    SeriesMemory, episode_label, load_memory, memory_path, save_memory, series_title,
)
from subtitle_translator.translate.analyze import apply_analysis


def test_series_title_strips_episode_and_tags():
    assert series_title("Bakusou Kyoudai Let's _ Go!! WGP - 01.mkv") == "Bakusou Kyoudai Let's _ Go!! WGP"
    assert series_title("[SubsPlease] Frieren - 12 (1080p) [ABCD].mkv") == "Frieren"
    assert series_title("Show.S01E03.mkv") == "Show"
    assert series_title("ドラマ 第05話.mp4") == "ドラマ"
    assert episode_label("Bakusou Kyoudai Let's _ Go!! WGP - 01.mkv") == "01"
    a, b = memory_path("D:/x/Show - 01.mkv"), memory_path("D:/x/Show - 02.mkv")
    assert a == b and a.name == "Show.series.md"


def _ep(tmp_path, name, chars, summary="s", glossary=None):
    ep = Episode(video_path=str(tmp_path / name), source="x.ass", src_lang="en",
                 cues=[Cue(1, 0, 1, "hi")], characters=chars, summary=summary)
    ep.glossary = glossary or {}
    return ep


def test_memory_round_trip_keeps_user_notes_and_merges(tmp_path):
    gou = Character("gou", "Gou", "โก", "male", "พระเอก", ["น้องของเรตสึ"], {"Gou-kun": "โก"}, "#FBBF24")
    ep1 = _ep(tmp_path, "Show - 01.mkv", [gou], "ตอนแรก", {"Cyclone Magnum": "ไซโคลนแม็กนั่ม"})
    mem = load_memory(ep1.video_path)
    mem.merge_episode(ep1)
    path = save_memory(ep1.video_path, mem)
    text = path.read_text(encoding="utf-8")
    assert "**โก** (Gou, `gou`)" in text and "### ตอน 01" in text and "Cyclone Magnum → ไซโคลนแม็กนั่ม" in text

    # the user writes notes by hand; the next save must keep them
    path.write_text(text.replace("(เขียนอะไรก็ได้ตรงนี้", "ใช้คำว่า 'มินิโฟร์วีล' เสมอ\n(เขียนอะไรก็ได้ตรงนี้", 1)
                    .replace(" — โปรแกรมจะส่งให้ AI ทุกตอน)", ""), encoding="utf-8")
    mem2 = load_memory(tmp_path / "Show - 02.mkv")
    assert "มินิโฟร์วีล" in mem2.notes
    retsu = Character("retsu", "Retsu", "เร็ตสึ", "male", "พี่ชาย", [], {}, "#000000")
    gou_again = Character("gou", "Gou", "โกะ", "unknown", "", ["คู่แข่งของเจ"], {}, "#000000")
    ep2 = _ep(tmp_path, "Show - 02.mkv", [gou_again, retsu], "ตอนสอง")
    mem2.merge_episode(ep2)
    save_memory(ep2.video_path, mem2)
    mem3 = load_memory(ep2.video_path)
    g = next(c for c in mem3.characters if c.key == "gou")
    assert g.name_th == "โก" and g.gender == "male" and g.color == "#FBBF24"
    assert g.relations == ["น้องของเรตสึ", "คู่แข่งของเจ"]
    assert [c.key for c in mem3.characters] == ["gou", "retsu"]
    assert [e.label for e in mem3.episodes] == ["01", "02"]
    assert "มินิโฟร์วีล" in mem3.notes

    block = mem3.prompt_block(exclude_file="Show - 02.mkv")
    assert "SERIES MEMORY" in block and "ตอนแรก" in block and "ตอนสอง" not in block
    assert "มินิโฟร์วีล" in block


def test_known_characters_keep_colour_and_thai_name(tmp_path):
    known = [Character("gou", "Gou", "โก", "male", color="#FBBF24")]
    ep = _ep(tmp_path, "Show - 02.mkv", [])
    data = {"summary": "s", "glossary": [{"source": "WGP", "thai": "ดับเบิลยูจีพี"}], "cues": [],
            "characters": [
                {"key": "gou", "name_src": "Gou", "name_th": "โกะ", "gender": "male", "role": "",
                 "relations": [], "address_terms": []},
                {"key": "jun", "name_src": "Jun", "name_th": "จุน", "gender": "female", "role": "",
                 "relations": [], "address_terms": []}]}
    apply_analysis(ep, data, known)
    gou, jun = ep.characters
    assert (gou.name_th, gou.color) == ("โก", "#FBBF24")
    assert jun.color != "#FBBF24"
    assert ep.glossary == {"WGP": "ดับเบิลยูจีพี"}


def test_unwritable_folder_does_not_raise(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("ro")))
    assert save_memory(tmp_path / "Show - 01.mkv", SeriesMemory(title="Show")) is None
