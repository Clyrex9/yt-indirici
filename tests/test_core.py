import os
import unittest

import yt_dlp

from ytindirici.downloader import OUTTMPL, Options, build_params
from ytindirici.utils import format_duration, format_ranges, parse_ranges


class UtilsTest(unittest.TestCase):
    def test_format_ranges(self):
        self.assertEqual(format_ranges([1, 2, 3, 7, 9, 10]), "1-3,7,9-10")
        self.assertEqual(format_ranges([5]), "5")
        self.assertEqual(format_ranges([3, 1, 2, 2]), "1-3")

    def test_parse_ranges(self):
        self.assertEqual(parse_ranges("1-3, 7, 10-9"), {1, 2, 3, 7, 9, 10})
        self.assertEqual(parse_ranges(""), set())
        with self.assertRaises(ValueError):
            parse_ranges("abc")

    def test_round_trip(self):
        nums = {1, 2, 3, 8, 20, 21}
        self.assertEqual(parse_ranges(format_ranges(nums)), nums)

    def test_format_duration(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3725), "1:02:05")
        self.assertEqual(format_duration(None), "")


class ParamsTest(unittest.TestCase):
    def test_audio(self):
        p = build_params(Options(folder="x", quality="mp3", embed=False))
        self.assertEqual(p["postprocessors"][0]["preferredcodec"], "mp3")
        self.assertNotIn("merge_output_format", p)

    def test_video_mp4_prefers_h264(self):
        p = build_params(Options(folder="x", quality="720"))
        self.assertIn("[height<=720]", p["format"])
        self.assertEqual(p["merge_output_format"], "mp4")
        self.assertIn("vcodec:h264", p["format_sort"])

    def test_selection_only_with_playlist(self):
        self.assertEqual(build_params(Options(folder="x", items=[1, 2, 5]))["playlist_items"], "1-2,5")
        self.assertNotIn("playlist_items", build_params(Options(folder="x", playlist=False, items=[1])))

    def test_output_paths(self):
        ydl = yt_dlp.YoutubeDL({"outtmpl": OUTTMPL, "paths": {"home": "dl"}, "quiet": True})
        single = {"id": "a", "title": "Video", "ext": "mp4"}
        in_list = dict(single, playlist_title="My List", playlist_index=7, playlist="My List")
        self.assertEqual(ydl.prepare_filename(single), os.path.join("dl", "Video.mp4"))
        self.assertEqual(ydl.prepare_filename(in_list), os.path.join("dl", "My List", "007 - Video.mp4"))


if __name__ == "__main__":
    unittest.main()
