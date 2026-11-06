from subtitle_translator.core.models import Cue, Episode


def make_episode(n: int = 5, lang: str = "ja") -> Episode:
    cues = [
        Cue(id=i, start=float(i * 2), end=float(i * 2 + 1.5), src=f"line {i}")
        for i in range(1, n + 1)
    ]
    return Episode(video_path="C:/v/ep01.mkv", source="C:/v/ep01.ass", src_lang=lang, cues=cues)
