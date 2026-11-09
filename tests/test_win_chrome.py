"""Barra de título escura: pede o atributo certo ao DWM e nunca quebra a janela."""
import sys
import unittest
from unittest.mock import Mock, patch

from frontend import win_chrome


class WinChromeTests(unittest.TestCase):
    def test_colorref_is_bgr(self):
        self.assertEqual(win_chrome._colorref("#14141a"), 0x1A1414)
        self.assertEqual(win_chrome._colorref("#ff0000"), 0x0000FF)

    @unittest.skipUnless(sys.platform == "win32", "DWM só existe no Windows")
    def test_requests_dark_mode_then_win11_colours(self):
        dwm = Mock()
        dwm.DwmSetWindowAttribute.return_value = 0
        widget = Mock(winId=Mock(return_value=1234))
        with patch("ctypes.windll") as windll:
            windll.dwmapi = dwm
            self.assertTrue(win_chrome.apply_dark_titlebar(widget))
        attrs = [c.args[1].value for c in dwm.DwmSetWindowAttribute.call_args_list]
        self.assertEqual(attrs, [20, 35, 34])

    @unittest.skipUnless(sys.platform == "win32", "DWM só existe no Windows")
    def test_falls_back_to_old_attribute_and_never_raises(self):
        dwm = Mock()
        dwm.DwmSetWindowAttribute.side_effect = [1, 0, 1, 1]   # 20 recusado, 19 aceito
        widget = Mock(winId=Mock(return_value=1234))
        with patch("ctypes.windll") as windll:
            windll.dwmapi = dwm
            self.assertTrue(win_chrome.apply_dark_titlebar(widget))
        broken = Mock(winId=Mock(side_effect=RuntimeError("sem janela nativa")))
        self.assertFalse(win_chrome.apply_dark_titlebar(broken))

    def test_other_systems_are_left_alone(self):
        with patch.object(win_chrome.sys, "platform", "linux"):
            self.assertFalse(win_chrome.apply_dark_titlebar(Mock()))


if __name__ == "__main__":
    unittest.main()
