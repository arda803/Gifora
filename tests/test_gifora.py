import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gifora  # noqa: E402


def make_gif(path):
    frames = [Image.new("RGBA", (120, 60), color) for color in ("red", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


class GiforaUtilityTests(unittest.TestCase):
    def test_drop_filter_accepts_supported_files_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "src") as temp_dir:
            gif_path = make_gif(Path(temp_dir) / "loop.gif")
            other_path = Path(temp_dir) / "notes.txt"
            other_path.write_text("not an image", encoding="utf-8")
            self.assertEqual(gifora.supported_drop_paths([gif_path, gif_path, other_path, Path(temp_dir) / "missing.webp"]), [str(gif_path)])

    def test_drop_payload_keeps_paths_with_spaces_intact(self):
        interpreter = tk.Tcl()
        payload = "{C:/GIF Library/loop one.gif} C:/Other/loop2.webp"
        self.assertEqual(
            gifora.parse_drop_data(interpreter, payload),
            ["C:/GIF Library/loop one.gif", "C:/Other/loop2.webp"],
        )

    def test_premultiplied_resize_avoids_magenta_transparent_edge(self):
        image = Image.new("RGBA", (2, 1))
        image.putdata([(255, 0, 255, 0), (255, 255, 255, 255)])
        resized = gifora.resize_rgba_frame(image, (8, 1))
        edge = resized.getpixel((3, 0))
        self.assertGreater(edge[3], 0)
        self.assertGreater(edge[1], 200)
        self.assertGreater(edge[2], 200)


class OverlayTestApp:
    def __init__(self, root):
        self.root = root
        self.monitors = gifora.get_monitors(root.winfo_screenwidth(), root.winfo_screenheight())
        self.last_position = None

    def move_overlay(self, overlay, x, y):
        gifora.GiforaApp.move_overlay(self, overlay, x, y)

    def monitor_for_point(self, x, y):
        return gifora.GiforaApp.monitor_for_point(self, x, y)

    def snap_overlay(self, overlay, x, y):
        return gifora.GiforaApp.snap_overlay(self, overlay, x, y)

    def update_position_label(self, item_id):
        self.last_position = (item_id, self.overlay.position, self.overlay.manual_xy)

    def save_config(self):
        pass

    def on_overlay_error(self, _item_id, error):
        raise AssertionError(f"GIF decoder error: {error}")


class GiforaOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk desktop session unavailable: {exc}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "src")
        self.gif_path = make_gif(Path(self.temp.name) / "animated.gif")
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = OverlayTestApp(self.root)
        self.overlay = gifora.GifOverlay(
            self.app, "test-overlay", str(self.gif_path), "Sol", 120
        )
        self.app.overlay = self.overlay
        self.pump_until(lambda: self.overlay.image_ref is not None)

    def tearDown(self):
        self.overlay.close()
        self.root.destroy()
        time.sleep(0.15)
        self.temp.cleanup()

    def pump_until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for the overlay event loop")

    def test_position_presets_move_the_overlay_window(self):
        bounds = self.app.monitors[0]
        self.overlay.set_position("Sağ alt")
        self.root.update()
        self.assertGreaterEqual(self.overlay.win.winfo_x(), bounds["right"] - self.overlay.win.winfo_width() - 32)
        self.assertGreaterEqual(self.overlay.win.winfo_y(), bounds["bottom"] - self.overlay.win.winfo_height() - 32)
        self.assertEqual(self.overlay.position, "Sağ alt")

    def test_drag_moves_overlay_and_records_custom_position(self):
        self.overlay.set_position("Sol üst")
        self.root.update()
        start = (self.overlay.win.winfo_x(), self.overlay.win.winfo_y())
        self.overlay._start_drag(SimpleNamespace(x_root=500, y_root=500))
        self.overlay._drag(SimpleNamespace(x_root=700, y_root=620))
        self.root.update()
        self.overlay._end_drag(SimpleNamespace())
        self.root.update()
        end = (self.overlay.win.winfo_x(), self.overlay.win.winfo_y())
        self.assertGreaterEqual(end[0], start[0] + 150)
        self.assertGreaterEqual(end[1], start[1] + 70)
        self.assertEqual(self.overlay.position, "Özel")
        self.assertEqual(self.overlay.manual_xy, end)

    def test_locked_overlay_does_not_move(self):
        self.overlay.set_position("Sol üst")
        self.overlay.set_locked(True)
        start = (self.overlay.win.winfo_x(), self.overlay.win.winfo_y())
        self.overlay._start_drag(SimpleNamespace(x_root=500, y_root=500))
        self.overlay._drag(SimpleNamespace(x_root=700, y_root=620))
        self.overlay._end_drag(SimpleNamespace())
        self.root.update()
        self.assertEqual((self.overlay.win.winfo_x(), self.overlay.win.winfo_y()), start)

    def test_animation_plays_and_width_preserves_aspect_ratio(self):
        photo = self.overlay.image_ref._PhotoImage__photo
        first = photo.get(10, 10)
        self.pump_until(lambda: photo.get(10, 10) != first)
        self.overlay.set_width(160)
        self.pump_until(lambda: self.overlay.image_ref.width() == 160)
        self.assertEqual(self.overlay.image_ref.height(), 80)

    def test_animated_webp_and_apng_are_decoded(self):
        frames = [Image.new("RGBA", (120, 60), color) for color in ("green", "yellow")]
        for suffix, image_format in (("webp", "WEBP"), ("png", "PNG")):
            path = Path(self.temp.name) / f"animated.{suffix}"
            frames[0].save(path, format=image_format, save_all=True, append_images=frames[1:], duration=100, loop=0)
            with Image.open(path) as image:
                self.assertEqual(image.format, image_format)
                self.assertGreaterEqual(image.n_frames, 2)
            self.overlay.image_ref = None
            self.overlay.load(str(path), 120)
            self.pump_until(lambda: self.overlay.image_ref is not None and not self.overlay._loading)
            self.assertEqual((self.overlay.image_ref.width(), self.overlay.image_ref.height()), (120, 60))

    @unittest.skipUnless(sys.platform == "win32", "Windows window-style API only")
    def test_clickthrough_can_be_enabled_and_disabled(self):
        self.assertTrue(self.overlay.set_clickthrough(True))
        self.assertTrue(self.overlay.clickthrough)
        self.assertTrue(self.overlay.set_clickthrough(False))
        self.assertFalse(self.overlay.clickthrough)


class GiforaAppIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk desktop session unavailable: {exc}")

    def test_settings_and_layout_profile_round_trip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "src") as temp_dir:
            data_dir = Path(temp_dir) / "settings"
            gif_path = make_gif(Path(temp_dir) / "profile.gif")
            old_paths = (gifora.CONFIG_DIR, gifora.CONFIG_FILE, gifora.PROFILES_FILE)
            gifora.CONFIG_DIR = data_dir
            gifora.CONFIG_FILE = data_dir / "ayarlar.json"
            gifora.PROFILES_FILE = data_dir / "duzenler.json"
            root = tk.Tk()
            app = None
            try:
                app = gifora.GiforaApp(root)
                app.add_gif(str(gif_path))
                item = app.items[0]
                overlay = app.overlays[item["id"]]
                deadline = time.monotonic() + 3
                while overlay.image_ref is None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertIsNotNone(overlay.image_ref)

                app.position_var.set("Sol üst")
                app._apply_position()
                root.update()
                self.assertEqual(overlay.position, "Sol üst")

                app._commit_width(item["id"], 160)
                deadline = time.monotonic() + 3
                while overlay.image_ref.width() != 160 and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertEqual((overlay.image_ref.width(), overlay.image_ref.height()), (160, 80))

                app.clickthrough_var.set(True)
                app._apply_clickthrough()
                self.assertTrue(item["clickthrough"])
                app.clickthrough_var.set(False)
                app._apply_clickthrough()
                self.assertFalse(item["clickthrough"])

                app.toggle_play(False)
                self.assertFalse(overlay.playing)
                app.toggle_play(True)
                self.assertTrue(overlay.playing)
                app.toggle_visible()
                self.assertFalse(overlay.visible)
                self.assertEqual(app.tree.item(item["id"], "values")[4], "Kapalı")
                app.toggle_visible()
                self.assertTrue(overlay.visible)
                root.update()
                cell = app.tree.bbox(item["id"], "#5")
                self.assertTrue(cell, "Visibility cell should be visible in the GIF list")
                app._on_tree_click(SimpleNamespace(x=cell[0] + cell[2] // 2, y=cell[1] + cell[3] // 2))
                self.assertFalse(overlay.visible, "Clicking the row visibility cell should hide only that GIF")
                self.assertEqual(app.tree.item(item["id"], "values")[4], "Kapalı")
                app.toggle_visible()
                self.assertTrue(overlay.visible)

                with patch.object(gifora.simpledialog, "askstring", return_value="Masaüstü"):
                    app.save_profile()
                self.assertIn("Masaüstü", app.profiles)
                app.save_config()
                saved = gifora.json.loads(gifora.CONFIG_FILE.read_text(encoding="utf-8"))
                self.assertEqual(len(saved["items"]), 1)
                self.assertEqual(saved["items"][0]["width"], 160)

                app.profile_var.set("Masaüstü")
                app.load_profile()
                deadline = time.monotonic() + 3
                loaded_overlay = app.overlays[app.items[0]["id"]]
                while loaded_overlay.image_ref is None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertEqual(app.items[0]["position"], "Sol üst")
                self.assertEqual(app.items[0]["width"], 160)
                self.assertIsNotNone(loaded_overlay.image_ref)
            finally:
                if app is not None:
                    app.quit()
                elif root.winfo_exists():
                    root.destroy()
                gifora.CONFIG_DIR, gifora.CONFIG_FILE, gifora.PROFILES_FILE = old_paths

    def test_library_search_and_bulk_controls(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "src") as temp_dir:
            data_dir = Path(temp_dir) / "settings"
            first_path = make_gif(Path(temp_dir) / "first-loop.gif")
            second_path = make_gif(Path(temp_dir) / "second-loop.gif")
            old_paths = (gifora.CONFIG_DIR, gifora.CONFIG_FILE, gifora.PROFILES_FILE)
            gifora.CONFIG_DIR = data_dir
            gifora.CONFIG_FILE = data_dir / "ayarlar.json"
            gifora.PROFILES_FILE = data_dir / "duzenler.json"
            root = tk.Tk()
            app = None
            try:
                app = gifora.GiforaApp(root)
                app.add_gif(str(first_path))
                app.add_gif(str(second_path))
                self.assertEqual(len(app.items), 2)
                self.assertEqual(len(app.tree.get_children()), 2)

                app.set_all_visible(False)
                self.assertTrue(all(not item["visible"] for item in app.items))
                self.assertTrue(all(not overlay.visible for overlay in app.overlays.values()))
                app.set_all_visible(True)
                self.assertTrue(all(item["visible"] for item in app.items))
                app.set_all_playing(False)
                self.assertTrue(all(not item["playing"] for item in app.items))
                app.set_all_playing(True)
                self.assertTrue(all(item["playing"] for item in app.items))

                app.search_var.set("second-loop")
                root.update()
                self.assertEqual(app.tree.get_children(), (app.items[1]["id"],))
                app.search_var.set("no-such-gif")
                root.update()
                self.assertEqual(app.tree.get_children(), ())
                self.assertIsNone(app.selected_id)
                app.search_var.set("")
                root.update()
                self.assertEqual(len(app.tree.get_children()), 2)
            finally:
                if app is not None:
                    app.quit()
                elif root.winfo_exists():
                    root.destroy()
                gifora.CONFIG_DIR, gifora.CONFIG_FILE, gifora.PROFILES_FILE = old_paths


if __name__ == "__main__":
    unittest.main()
