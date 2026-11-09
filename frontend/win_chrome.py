"""Moldura do Windows no tom do design: barra de título escura.

No Windows 10 (20H1+) e 11, DWMWA_USE_IMMERSIVE_DARK_MODE deixa a barra de
título escura; no Windows 11 a legenda e a borda também recebem a cor do
design. Em outros sistemas, ou se o DWM recusar, nada muda (a janela segue
com a moldura padrão) — nunca levanta exceção.
"""
import sys

_DARK_MODE = 20            # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 20H1+ e Win11)
_DARK_MODE_OLD = 19        # mesmo atributo em builds 18362–18363
_BORDER_COLOR = 34         # Win11
_CAPTION_COLOR = 35        # Win11


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def apply_dark_titlebar(widget, caption: str = "#14141a", border: str = "#26262c") -> bool:
    """Pede ao DWM a moldura escura. Devolve True se o modo escuro foi aceito."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwm = ctypes.windll.dwmapi
        hwnd = wintypes.HWND(int(widget.winId()))

        def set_attr(attr: int, value: int) -> bool:
            data = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, wintypes.DWORD(attr), ctypes.byref(data),
                                             ctypes.sizeof(data)) == 0

        ok = set_attr(_DARK_MODE, 1) or set_attr(_DARK_MODE_OLD, 1)
        set_attr(_CAPTION_COLOR, _colorref(caption))     # ignorado antes do Win11
        set_attr(_BORDER_COLOR, _colorref(border))
        return ok
    except Exception:  # noqa: BLE001 - cosmético: a janela abre do mesmo jeito
        return False
