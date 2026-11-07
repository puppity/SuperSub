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
    known = [Character("gou", "Gou", "โก", "male", color="#FBBF24", self_pronoun="ข้า")]
    ep = _ep(tmp_path, "Show - 02.mkv", [])
    data = {"series_title": "", "episode_number": 0, "summary": "s", "glossary": [{"source": "WGP", "thai": "ดับเบิลยูจีพี"}], "cues": [],
            "characters": [
                {"key": "gou", "name_src": "Gou", "name_th": "โกะ", "gender": "male", "role": "",
                 "relations": [], "address_terms": [], "alignment": "villain",
                 "self_pronoun": "กู", "you_pronoun": "มึง", "speech_style": ""},
                {"key": "jun", "name_src": "Jun", "name_th": "จุน", "gender": "female", "role": "",
                 "relations": [], "address_terms": []}]}
    apply_analysis(ep, data, known)
    gou, jun = ep.characters
    assert (gou.name_th, gou.color) == ("โก", "#FBBF24")
    assert (gou.self_pronoun, gou.you_pronoun, gou.alignment) == ("ข้า", "มึง", "villain")
    assert jun.color != "#FBBF24"
    assert ep.glossary == {"WGP": "ดับเบิลยูจีพี"}


def test_unwritable_folder_does_not_raise(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("ro")))
    assert save_memory(tmp_path / "Show - 01.mkv", SeriesMemory(title="Show")) is None


def test_series_folder_collects_outputs_and_reads_legacy_memory(tmp_path):
    from subtitle_translator.core.series import legacy_memory_path, series_dir
    v = tmp_path / "Show - 01.mkv"
    assert series_dir(v) == tmp_path / "Show"
    assert series_dir(v, tmp_path / "out") == tmp_path / "out" / "Show"
    numbered = tmp_path / "หนังพี่บอล" / "01.mp4"           # bare episode number: folder is the title
    assert series_title(numbered) == "หนังพี่บอล" and series_dir(numbered) == numbered.parent
    old = legacy_memory_path(v)
    old.write_text(SeriesMemory(title="Show", notes="ใช้คำว่า ข้า").to_markdown(), encoding="utf-8")
    mem = load_memory(v)
    assert mem.notes == "ใช้คำว่า ข้า"                       # older file beside the video still read
    saved = save_memory(v, mem)
    assert saved == tmp_path / "Show" / "Show.series.md" and old.exists()
    assert load_memory(v).notes == "ใช้คำว่า ข้า"


def test_old_memory_without_voice_fields_still_loads_and_markdown_shows_voice():
    import json
    old = {"version": 1, "title": "Show", "glossary": {}, "episodes": [],
           "characters": [{"key": "a", "name_src": "A", "name_th": "เอ", "gender": "male", "role": "",
                           "relations": [], "address_terms": {}, "color": "#FFFFFF", "retired": 1}]}
    text = "<!-- series-data\n" + json.dumps(old) + "\n-->"
    mem = SeriesMemory.from_markdown(text, "Show")
    assert mem.characters[0].self_pronoun == "" and mem.characters[0].alignment == "unknown"
    mem.characters[0].alignment, mem.characters[0].self_pronoun = "villain", "ข้า"
    md = mem.to_markdown()
    assert "ฝ่ายร้าย" in md and "แทนตัว ข้า" in md


def test_ai_series_title_names_the_folder_and_episodes_sort(tmp_path):
    from subtitle_translator.core.series import (display_title, episode_number, output_stem,
                                                 remember_series, series_dir)
    from subtitle_translator.export import output_paths
    a = tmp_path / "[Grp] sousou no frieren 05 [1080p].mkv"
    assert display_title(a) == "sousou no frieren"            # nothing known yet: the file name
    assert remember_series(a, "Frieren: Beyond Journey's End", 5) == "Frieren: Beyond Journey's End"
    assert series_dir(a, tmp_path / "out") == tmp_path / "out" / "Frieren_ Beyond Journey's End"
    assert output_stem(a) == "Frieren_ Beyond Journey's End - EP05"
    # A later answer for the same file naming never moves the folder.
    b = tmp_path / "[Grp] sousou no frieren 12 [1080p].mkv"
    assert remember_series(b, "Sousou no Frieren", 12) == "Frieren: Beyond Journey's End"
    assert output_paths(b, str(tmp_path))[0].name == "Frieren_ Beyond Journey's End - EP12.th.srt"
    # Another release group's naming of the same show joins the same folder.
    c = tmp_path / "Frieren Beyond Journeys End - 07.mkv"
    assert remember_series(c, "frieren: beyond journey's end", 7) == "Frieren: Beyond Journey's End"
    # No number in the file name: the AI's episode number is used.
    d = tmp_path / "Mystery Show finale.mkv"
    assert episode_number(d) is None and output_stem(d) == "Mystery Show finale"
    remember_series(d, "Mystery Show", 24)
    assert episode_number(d) == 24 and output_stem(d) == "Mystery Show - EP24"


def test_memory_follows_the_ai_title(tmp_path):
    from subtitle_translator.core.series import (SeriesMemory, load_memory, memory_path,
                                                 remember_series, save_memory)
    out = tmp_path / "out"
    ep1 = tmp_path / "show x - 01.mkv"
    mem = SeriesMemory(title="show x", glossary={"WGP": "ดับเบิลยูจีพี"})
    save_memory(ep1, mem, out)                    # written before the AI named the series
    remember_series(ep1, "Show X Official", 1)
    ep2 = tmp_path / "show x - 02.mkv"
    got = load_memory(ep2, out)                   # still found under the old folder
    assert got.title == "Show X Official" and got.glossary == {"WGP": "ดับเบิลยูจีพี"}
    save_memory(ep2, got, out)
    assert memory_path(ep2, out) == out / "Show X Official" / "Show X Official.series.md"
    assert memory_path(ep2, out).exists()
