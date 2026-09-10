"""Tests for VideoBuilder and ShortsBuilder classes (Modules 11 and 12)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from builders.video_builder import VideoBuilder
from builders.shorts_builder import ShortsBuilder, ShortsOverLimitError, CARD_STYLES


class TestVideoBuilderClass:

    @pytest.fixture
    def vb(self, tmp_path):
        return VideoBuilder(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "videos"
        VideoBuilder(output_dir=d)
        assert d.exists()

    def test_validate_duration_ok(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="150.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is True
        assert dur == 150.0

    def test_validate_duration_too_short(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="60.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is False

    def test_validate_duration_too_long(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="200.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is False

    def test_add_watermark_returns_video_path_on_failure(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=1, stderr=b"error")
        with patch("subprocess.run", return_value=mock_result):
            result = vb.add_watermark(f)
        assert result == f

    def test_mix_audio_returns_video_path_on_failure(self, vb, tmp_path):
        video = tmp_path / "video.mp4"
        music = tmp_path / "music.mp3"
        video.write_bytes(b"video")
        music.write_bytes(b"music")
        mock_result = MagicMock(returncode=1, stderr=b"error")
        with patch("subprocess.run", return_value=mock_result):
            result = vb.mix_audio(video, music)
        assert result == video

    def test_burn_captions_returns_original_on_empty(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        result = vb.burn_captions(f, [])
        assert result == f

    def test_burn_captions_calls_ffmpeg(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        out = tmp_path / "captioned_video.mp4"
        out.write_bytes(b"output")
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            result = vb.burn_captions(f, [(0.0, 5.0, "Hello world")])
        assert result is not None

    def test_export_raises_on_failure(self, vb, tmp_path):
        f = tmp_path / "input.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=1, stderr=b"error msg")
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError):
                vb.export(f)


class TestShortsBuilderClass:

    @pytest.fixture
    def sb(self, tmp_path):
        return ShortsBuilder(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "shorts"
        ShortsBuilder(output_dir=d)
        assert d.exists()

    def test_add_shorts_tag_appends_when_missing(self, sb):
        title = "Market Crash Today"
        result = sb.add_shorts_tag(title)
        assert "#Shorts" in result

    def test_add_shorts_tag_no_double_append(self, sb):
        title = "Market Crash #Shorts"
        result = sb.add_shorts_tag(title)
        assert result.count("#Shorts") == 1

    def test_add_shorts_tag_handles_lowercase(self, sb):
        title = "Market Crash #shorts"
        result = sb.add_shorts_tag(title)
        assert "#Shorts" not in result or result.count("#shorts") == 1

    def test_parse_cards_from_section_markers(self, sb):
        script = "[HOOK]\nOpen strong\n[STAT]\n$1.2T GDP\n[CONTEXT]\nFed meeting\n[CALLOUT]\nBig deal\n[CTA]\nSubscribe"
        cards = sb.parse_cards(script)
        assert len(cards) >= 4
        types = [c["type"] for c in cards]
        assert "hook" in types or "stat" in types

    def test_parse_cards_from_card_markers(self, sb):
        script = "[CARD 1]\nHook text\n[CARD 2]\n$500B\n[CARD 3]\nContext\n[CARD 4]\nCallout\n[CARD 5]\nCTA"
        cards = sb.parse_cards(script)
        assert len(cards) == 5

    def test_parse_cards_fallback_on_unstructured(self, sb):
        script = "Line 1\nLine 2\nLine 3"
        cards = sb.parse_cards(script)
        assert len(cards) == 5  # fallback always returns 5

    def test_parse_cards_max_5(self, sb):
        script = "\n".join(f"[CARD {i}]\nText {i}" for i in range(1, 10))
        cards = sb.parse_cards(script)
        assert len(cards) <= 5

    def test_card_styles_has_5_types(self):
        assert len(CARD_STYLES) == 5
        assert "hook" in CARD_STYLES
        assert "cta" in CARD_STYLES

    def test_add_background_music_returns_original_when_no_music_dir(self, sb, tmp_path):
        video = tmp_path / "short.mp4"
        video.write_bytes(b"fake")
        with patch("config.settings.settings") as mock_settings:
            mock_settings.assets_dir = tmp_path  # no music subdir
            result = sb.add_background_music(video)
        assert result == video

    def test_music_history_persists_7_entries(self, sb):
        history = [f"track{i}.mp3" for i in range(10)]
        sb._save_music_history(history)
        loaded = sb._load_music_history()
        assert len(loaded) == 7

    def test_shorts_over_limit_error_is_exception(self):
        with pytest.raises(ShortsOverLimitError):
            raise ShortsOverLimitError("Over 60s")


# ── Shorts cards ─────────────────────────────────────────────────────────────

class TestShortsCards:
    """
    The old cards were centred paragraphs on a flat colour field — a slide
    deck rotated ninety degrees. These put imagery behind a caption band,
    the way vertical finance video actually looks.
    """

    @staticmethod
    def _photo(tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "photo.jpg"
        Image.new("RGB", (1600, 900), (70, 80, 120)).save(path)
        return path

    def test_a_card_renders_at_vertical_canvas_size(self, tmp_path):
        from config.settings import settings
        from builders.shorts_cards import render_card
        Image = pytest.importorskip("PIL.Image")
        path = render_card("Markets moved today.", tmp_path / "c.png")
        assert Image.open(path).size == (settings.shorts_width, settings.shorts_height)

    def test_a_photo_background_is_used_and_dimmed(self, tmp_path):
        """Undimmed, the caption stops being readable at a glance."""
        from builders.shorts_cards import render_card, PHOTO_DIM
        Image = pytest.importorskip("PIL.Image")
        ImageStat = pytest.importorskip("PIL.ImageStat")
        photo = self._photo(tmp_path)
        card = render_card("Some copy.", tmp_path / "c.png", photo=photo)
        original = ImageStat.Stat(Image.open(photo).convert("L")).mean[0]
        rendered = ImageStat.Stat(Image.open(card).convert("L")).mean[0]
        assert rendered < original
        assert PHOTO_DIM < 1.0

    def test_an_unreadable_photo_falls_back_to_colour(self, tmp_path):
        from builders.shorts_cards import render_card
        Image = pytest.importorskip("PIL.Image")
        broken = tmp_path / "broken.jpg"
        broken.write_text("not an image")
        path = render_card("Copy.", tmp_path / "c.png", photo=broken)
        assert Image.open(path).size[0] > 0

    def test_a_positive_stat_differs_from_a_negative_one(self, tmp_path):
        from builders.shorts_cards import render_card
        Image = pytest.importorskip("PIL.Image")
        up = render_card("NVIDIA", tmp_path / "up.png", kind="stat", stat="+9.1%")
        down = render_card("NVIDIA", tmp_path / "dn.png", kind="stat", stat="-9.1%")
        assert Image.open(up).tobytes() != Image.open(down).tobytes()

    def test_nothing_readable_sits_under_youtube_chrome(self, tmp_path):
        """
        The Shorts UI — title, channel row, action buttons — is drawn over
        the bottom of the frame, so caption text placed there is invisible in
        the app however good the still looks. Checked by rendering and
        finding the white type, not by reading the source: the constants can
        be right and the layout still wrong.
        """
        from builders.shorts_cards import render_card, SAFE_TOP, SAFE_BOTTOM
        Image = pytest.importorskip("PIL.Image")

        path = render_card(
            "Ten-year yields pushed to 4.31 percent as traders priced out a cut.",
            tmp_path / "c.png",
        )
        img = Image.Image.convert(Image.open(path), "L")
        width, height = img.size
        # Only the caption is near-white: the accent strip, the progress bar
        # and the muted handle all sit well below this threshold.
        rows = [y for y in range(height)
                if max(img.crop((0, y, width, y + 1)).getdata()) > 200]

        assert rows, "no caption text rendered"
        assert min(rows) >= height * SAFE_TOP - 2
        assert max(rows) <= height * SAFE_BOTTOM

    def test_short_and_long_captions_both_stay_centred(self, tmp_path):
        """
        A one-line caption pinned to a fixed top leaves the lower half of the
        frame dead. Both lengths should sit around the middle of the safe
        zone, not both start at the same y.
        """
        from builders.shorts_cards import render_card, SAFE_TOP, SAFE_BOTTOM
        Image = pytest.importorskip("PIL.Image")

        def first_text_row(text, name):
            img = Image.Image.convert(Image.open(render_card(text, tmp_path / name)), "L")
            width, height = img.size
            return next(y for y in range(height)
                        if max(img.crop((0, y, width, y + 1)).getdata()) > 200), height

        short_row, height = first_text_row("Yields jumped.", "short.png")
        long_row, _ = first_text_row(
            "Ten-year yields pushed to 4.31 percent as traders priced out a "
            "December cut and the dollar firmed against every major peer.",
            "long.png",
        )
        assert short_row > long_row, "short caption should sit lower than a long one"
        middle = height * (SAFE_TOP + SAFE_BOTTOM) / 2
        assert abs(short_row - middle) < height * 0.12

    def test_long_copy_wraps_rather_than_overflowing(self, tmp_path):
        from builders.shorts_cards import render_card
        Image = pytest.importorskip("PIL.Image")
        path = render_card("word " * 120, tmp_path / "c.png")
        assert Image.open(path).size[1] > 0

    def test_the_sequence_covers_the_runtime(self, tmp_path):
        from builders.shorts_cards import build_card_sequence
        pytest.importorskip("PIL.Image")
        cards = [{"text": "a", "kind": "hook"}, {"text": "NVDA", "kind": "stat",
                  "stat": "+9.1%"}, {"text": "c", "kind": "cta"}]
        seq = build_card_sequence(cards, tmp_path, 45.0)
        assert len(seq) == 3
        assert sum(d for _, d in seq) == pytest.approx(45.0, abs=0.05)

    def test_photos_rotate_rather_than_repeat(self, tmp_path):
        """The same picture on every card is the flat-colour problem again."""
        from builders.shorts_cards import build_card_sequence
        Image = pytest.importorskip("PIL.Image")
        photos = []
        for i, shade in enumerate([(20, 60, 120), (120, 60, 20)]):
            p = tmp_path / f"p{i}.jpg"
            Image.new("RGB", (1600, 900), shade).save(p)
            photos.append(p)
        cards = [{"text": f"card {i}", "kind": "context"} for i in range(2)]
        seq = build_card_sequence(cards, tmp_path, 30.0, photos=photos)
        assert Image.open(seq[0][0]).tobytes() != Image.open(seq[1][0]).tobytes()

    def test_no_cards_returns_empty_for_the_caller_to_fall_back(self, tmp_path):
        from builders.shorts_cards import build_card_sequence
        assert build_card_sequence([], tmp_path, 45.0) == []


# ── Shorts card plan ─────────────────────────────────────────────────────────

class TestShortsCardPlan:
    """
    The plan decides the Short's rhythm. Five cards over fifty seconds is a
    ten-second hold each, which reads as a slideshow no matter how good the
    individual card is.
    """

    @staticmethod
    def _assets(script="", key_stat="S&P 500 -0.55%", hook="Wall Street gave back gains"):
        from builders.shorts_builder import ShortsAssets
        return ShortsAssets(
            audio_path=Path("/dev/null"), chart_paths=[], thumbnail_path=None,
            script=script, title="Market Recap", hook_text=hook,
            key_stat=key_stat, ticker="SPY", sentiment="bearish",
        )

    def test_card_count_follows_the_clip_length(self):
        from builders.shorts_builder import _card_count, CARD_SECONDS, MAX_CARDS
        assert _card_count(50) == round(50 / CARD_SECONDS)
        assert _card_count(5) == 5, "never fewer than the five-beat structure"
        assert _card_count(600) == MAX_CARDS, "capped so rendering stays cheap"

    def test_a_fifty_second_short_cuts_every_few_seconds(self):
        from builders.shorts_builder import _cards_from_assets, _card_count, CARD_SECONDS
        script = " ".join(
            f"Sector {n} climbed {n}.5 percent as buyers returned to the tape."
            for n in range(1, 15)
        )
        cards = _cards_from_assets(self._assets(script), _card_count(50))
        assert 50 / len(cards) <= CARD_SECONDS + 1.0

    def test_key_stat_becomes_a_hero_number_with_its_label(self):
        from builders.shorts_builder import _cards_from_assets
        cards = _cards_from_assets(self._assets(), 5)
        stat = next(c for c in cards if c["kind"] == "stat")
        assert stat["stat"] == "-0.55%"
        assert stat["text"] == "S&P 500"

    def test_a_bare_index_level_is_not_mistaken_for_the_headline_figure(self):
        """"500" in "S&P 500" is part of the name, not the number."""
        from builders.shorts_builder import _split_stat
        assert _split_stat("S&P 500 +0.26%") == ("S&P 500", "+0.26%")
        assert _split_stat("Nasdaq 100 -1.2%") == ("Nasdaq 100", "-1.2%")

    def test_long_sentences_are_cut_at_their_clause_joints(self):
        from builders.shorts_builder import _script_phrases
        phrases = _script_phrases(
            "Ten-year yields pushed to 4.31 percent as traders priced out a "
            "December cut, and the dollar firmed against every major peer.", 6
        )
        assert len(phrases) > 1
        assert all(len(p) <= 90 for p in phrases)

    def test_disclaimer_boilerplate_never_takes_a_card_slot(self):
        from builders.shorts_builder import _script_phrases
        script = (
            "The index closed down half a percent. This content is for "
            "informational purposes only and does not constitute financial "
            "advice. Narration is AI-generated."
        )
        phrases = _script_phrases(script, 6)
        joined = " ".join(phrases).lower()
        assert "financial advice" not in joined
        assert "ai-generated" not in joined

    def test_the_last_card_still_carries_the_disclaimer(self):
        from builders.shorts_builder import _cards_from_assets, SHORT_DISCLAIMER
        cards = _cards_from_assets(self._assets("Markets rose today."), 5)
        assert SHORT_DISCLAIMER in cards[-1]["text"]

    def test_body_beats_alternate_between_caption_and_number(self):
        """A run of identical text cards reads as one long card."""
        from builders.shorts_builder import _cards_from_assets
        script = " ".join(
            f"The {n} sector index rose {n}.4 percent on the session."
            for n in range(1, 10)
        )
        kinds = [c["kind"] for c in _cards_from_assets(self._assets(script), 12)]
        assert kinds.count("stat") >= 2

    def test_a_silent_placeholder_audio_path_is_not_offered_to_ffmpeg(self):
        """/dev/null is the historic no-voiceover marker; ffmpeg rejects it."""
        from builders.shorts_builder import _has_audio
        assert _has_audio(Path("/dev/null")) is False
        assert _has_audio(Path("/nonexistent/audio.mp3")) is False

    def test_parsed_cards_are_accepted_in_either_shape(self):
        from builders.shorts_builder import _normalise_card
        assert _normalise_card({"type": "context", "text": "Hi"})["kind"] == "context"
        promoted = _normalise_card({"type": "stat", "text": "Gold hit $2,410 an ounce"})
        assert promoted["stat"]
        assert len(promoted["text"]) < len("Gold hit $2,410 an ounce")

    def test_the_hook_is_not_repeated_as_the_first_body_card(self):
        """
        The hook is usually a tightened version of the script's opening
        sentence, so the two land back to back saying the same thing.
        """
        from builders.shorts_builder import _cards_from_assets
        cards = _cards_from_assets(self._assets(
            script=("Wall Street just gave back a week of gains. "
                    "Energy was the only sector in the green today."),
            hook="Wall Street gave back a week of gains",
        ), 6)
        texts = [c["text"].lower().rstrip(".") for c in cards]
        assert "wall street just gave back a week of gains" not in texts

    def test_the_headline_number_is_not_shown_twice(self):
        from builders.shorts_builder import _cards_from_assets
        cards = _cards_from_assets(self._assets(
            script=" ".join(["The index closed down 0.55 percent on the session."] * 1
                            + [f"Sector {n} rose {n}.4 percent in late trade."
                               for n in range(1, 8)]),
        ), 12)
        stats = [c["stat"] for c in cards if c["kind"] == "stat"]
        assert len(stats) == len(set(stats))

    def test_near_duplicate_detection_still_allows_distinct_copy(self):
        from builders.shorts_builder import _says_the_same_thing
        assert _says_the_same_thing(
            "Wall Street just gave back a week of gains.",
            "Wall Street gave back a week of gains",
        )
        assert not _says_the_same_thing(
            "Energy was the only sector in the green.",
            "Wall Street gave back a week of gains",
        )

    def test_an_undecodable_audio_file_falls_back_to_silence(self, tmp_path):
        """
        A truncated or half-written TTS file passes any size check and then
        fails the whole encode, because the command maps its audio stream
        explicitly. A Short with music instead of narration still publishes.
        """
        from builders.shorts_builder import _has_audio
        junk = tmp_path / "half_written.wav"
        junk.write_bytes(b"\x00" * 5000)
        assert _has_audio(junk) is False

    def test_real_audio_is_recognised(self, tmp_path):
        import subprocess
        from builders.shorts_builder import _has_audio
        wav = tmp_path / "vo.wav"
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1",
             "-ar", "44100", str(wav)],
            capture_output=True)
        if result.returncode != 0:
            pytest.skip("ffmpeg unavailable")
        assert _has_audio(wav) is True


class TestShortsHookCard:
    """
    Shorts drive 50% of this channel's views, so the hook is the frame that
    decides whether any of them are watched past the first second. It used
    to get the same small caption band as a body card.
    """

    def test_the_hook_is_set_far_larger_than_a_body_caption(self, tmp_path):
        from builders.shorts_cards import render_card, HOOK_MAX_FRACTION
        Image = pytest.importorskip("PIL.Image")

        def ink_rows(kind, name):
            path = render_card("Wall Street gave back gains", tmp_path / name,
                               kind=kind)
            img = Image.Image.convert(Image.open(path), "L")
            width, height = img.size
            return sum(1 for y in range(height)
                       if max(img.crop((0, y, width, y + 1)).getdata()) > 200)

        assert ink_rows("hook", "h.png") > ink_rows("context", "c.png")
        assert HOOK_MAX_FRACTION > 0.06

    def test_the_hook_sits_high_where_the_eye_lands(self, tmp_path):
        from builders.shorts_cards import render_card, SAFE_TOP, SAFE_BOTTOM
        Image = pytest.importorskip("PIL.Image")
        path = render_card("Wall Street gave back a week of gains",
                           tmp_path / "h.png", kind="hook")
        img = Image.Image.convert(Image.open(path), "L")
        width, height = img.size
        rows = [y for y in range(height)
                if max(img.crop((0, y, width, y + 1)).getdata()) > 200]
        assert rows
        assert min(rows) >= height * SAFE_TOP - 4
        assert min(rows) < height * (SAFE_TOP + SAFE_BOTTOM) / 2

    def test_long_hook_copy_shrinks_rather_than_overflowing(self, tmp_path):
        from builders.shorts_cards import (render_card, _fit_hook,
                                           HOOK_MAX_LINES, HOOK_MIN_FRACTION)
        from PIL import ImageDraw, Image as PILImage
        pytest.importorskip("PIL.Image")
        draw = ImageDraw.Draw(PILImage.new("RGB", (1080, 1920)))
        lines, font = _fit_hook(draw, "word " * 40, 900, 1920)
        assert len(lines) <= HOOK_MAX_LINES
        assert font.size >= int(1920 * HOOK_MIN_FRACTION) - 1
        # And it still renders.
        assert render_card("word " * 40, tmp_path / "h.png", kind="hook").exists()

    def test_a_short_hook_keeps_the_full_size(self, tmp_path):
        """
        Shrinking unconditionally would throw away the size advantage that
        makes the hook stop a scroll.
        """
        from builders.shorts_cards import _fit_hook, HOOK_MAX_FRACTION
        from PIL import ImageDraw, Image as PILImage
        pytest.importorskip("PIL.Image")
        draw = ImageDraw.Draw(PILImage.new("RGB", (1080, 1920)))
        _, font = _fit_hook(draw, "Yields jumped", 900, 1920)
        assert font.size == int(1920 * HOOK_MAX_FRACTION)


class TestShortsCardTiming:

    @staticmethod
    def _plan(count):
        cards = [{"text": "Hook line", "kind": "hook"}]
        cards += [{"text": f"Body beat number {n} on the session.",
                   "kind": "context"} for n in range(count - 1)]
        return cards

    def test_the_hook_holds_less_than_an_even_share(self, tmp_path):
        """
        Equal time put the first real number five seconds into a
        fifty-second Short. A hook only has to be read.
        """
        from builders.shorts_cards import build_card_sequence, HOOK_SECONDS
        pytest.importorskip("PIL.Image")
        seq = build_card_sequence(self._plan(10), tmp_path, 50.0)
        assert seq[0][1] == pytest.approx(HOOK_SECONDS)
        assert seq[1][1] > seq[0][1]

    def test_the_durations_still_sum_to_the_audio_length(self, tmp_path):
        """The video must not end before or after the narration."""
        from builders.shorts_cards import build_card_sequence
        pytest.importorskip("PIL.Image")
        seq = build_card_sequence(self._plan(12), tmp_path, 50.0)
        assert sum(s for _, s in seq) == pytest.approx(50.0, abs=0.05)

    def test_a_plan_with_no_hook_divides_evenly(self, tmp_path):
        from builders.shorts_cards import build_card_sequence
        pytest.importorskip("PIL.Image")
        cards = [{"text": f"Beat {n}", "kind": "context"} for n in range(5)]
        seq = build_card_sequence(cards, tmp_path, 40.0)
        assert all(s == pytest.approx(8.0) for _, s in seq)

    def test_a_too_short_plan_warns_rather_than_going_quietly_slow(self, tmp_path,
                                                                   caplog):
        import logging
        from builders import shorts_cards
        pytest.importorskip("PIL.Image")
        with caplog.at_level(logging.WARNING, logger=shorts_cards.__name__):
            shorts_cards.build_card_sequence(self._plan(4), tmp_path, 50.0)
        assert any("slideshow" in r.getMessage() for r in caplog.records)
