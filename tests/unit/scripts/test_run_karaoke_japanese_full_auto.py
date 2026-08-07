from scripts import run_karaoke_japanese_full_auto as entry


def test_japanese_entry_restricts_language(monkeypatch):
    captured = {}

    def fake(argv, *, allowed_languages):
        captured["languages"] = allowed_languages
        return 0

    monkeypatch.setattr(entry, "full_auto_main", fake)
    assert entry.main([]) == 0
    assert captured["languages"] == frozenset({"ja"})
