"""Draw anti-aliased text into a premultiplied-alpha Windows overlay."""

import ctypes
from ctypes import wintypes


class StartupInput(ctypes.Structure):
    _fields_ = [("version", wintypes.UINT), ("callback", ctypes.c_void_p),
                ("suppress_thread", wintypes.BOOL), ("suppress_codecs", wintypes.BOOL)]


class BitmapHeader(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                ("height", wintypes.LONG), ("planes", wintypes.WORD),
                ("bit_count", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image_size", wintypes.DWORD), ("x_resolution", wintypes.LONG),
                ("y_resolution", wintypes.LONG), ("colors_used", wintypes.DWORD),
                ("colors_important", wintypes.DWORD)]


class RectF(ctypes.Structure):
    _fields_ = [(name, ctypes.c_float) for name in ("x", "y", "width", "height")]


class BlendFunction(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ubyte) for name in ("operation", "flags", "opacity", "alpha_format")]


class AlphaText:
    def __init__(self, window):
        self.window = window
        self.cache = None
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.plus = ctypes.WinDLL("gdiplus")
        self._bind()
        self.token = ctypes.c_size_t()
        self._check(self.plus.GdiplusStartup(ctypes.byref(self.token),
                    ctypes.byref(StartupInput(1, None, False, False)), None))

    def _bind(self):
        pointer = ctypes.c_void_p
        out = ctypes.POINTER(pointer)
        real = ctypes.c_float
        signatures = {
            "GdiplusStartup": ([ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(StartupInput), pointer], ctypes.c_int),
            "GdiplusShutdown": ([ctypes.c_size_t], None),
            "GdipCreateBitmapFromScan0": ([ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, pointer, out], ctypes.c_int),
            "GdipGetImageGraphicsContext": ([pointer, out], ctypes.c_int),
            "GdipGraphicsClear": ([pointer, wintypes.UINT], ctypes.c_int),
            "GdipSetTextRenderingHint": ([pointer, ctypes.c_int], ctypes.c_int),
            "GdipCreateFontFamilyFromName": ([wintypes.LPCWSTR, pointer, out], ctypes.c_int),
            "GdipCreateFont": ([pointer, real, ctypes.c_int, ctypes.c_int, out], ctypes.c_int),
            "GdipStringFormatGetGenericTypographic": ([out], ctypes.c_int),
            "GdipSetStringFormatAlign": ([pointer, ctypes.c_int], ctypes.c_int),
            "GdipSetStringFormatLineAlign": ([pointer, ctypes.c_int], ctypes.c_int),
            "GdipSetStringFormatFlags": ([pointer, ctypes.c_int], ctypes.c_int),
            "GdipCreateSolidFill": ([wintypes.UINT, out], ctypes.c_int),
            "GdipDrawString": ([pointer, wintypes.LPCWSTR, ctypes.c_int, pointer,
                                ctypes.POINTER(RectF), pointer, pointer], ctypes.c_int),
            "GdipFillEllipse": ([pointer, pointer, real, real, real, real], ctypes.c_int),
            "GdipFlush": ([pointer, ctypes.c_int], ctypes.c_int),
            "GdipSaveImageToFile": ([pointer, wintypes.LPCWSTR, pointer, pointer], ctypes.c_int),
        }
        for name in ("GdipDeleteGraphics", "GdipDisposeImage", "GdipDeleteFontFamily",
                     "GdipDeleteFont", "GdipDeleteStringFormat", "GdipDeleteBrush"):
            signatures[name] = ([pointer], ctypes.c_int)
        for name, (arguments, result) in signatures.items():
            function = getattr(self.plus, name)
            function.argtypes, function.restype = arguments, result
        self.gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi.CreateDIBSection.argtypes = [wintypes.HDC, pointer, wintypes.UINT,
                                            out, wintypes.HANDLE, wintypes.DWORD]
        self.gdi.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi.SelectObject.restype = wintypes.HANDLE
        self.gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi.DeleteDC.argtypes = [wintypes.HDC]
        self.user.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            wintypes.DWORD, ctypes.POINTER(BlendFunction), wintypes.DWORD,
        ]
        self.user.UpdateLayeredWindow.restype = wintypes.BOOL

    @staticmethod
    def _check(status):
        if status:
            raise RuntimeError(f"Windows text rendering failed ({status})")

    def render(self, timer, percent, dpi, foreground, unlocked=False,
               background="#f3f3f3", stale=False, preview=None):
        key = (timer, percent, dpi, foreground, unlocked, background, stale)
        if key == self.cache and preview is None:
            return
        scale = dpi / 96
        width, height = round(214 * scale), round(30 * scale)
        dc = self.gdi.CreateCompatibleDC(None)
        if not dc:
            raise ctypes.WinError(ctypes.get_last_error())
        bitmap = previous = None
        cleanup = []
        try:
            header = BitmapHeader(ctypes.sizeof(BitmapHeader), width, -height, 1, 32)
            pixels = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(dc, ctypes.byref(header), 0,
                                               ctypes.byref(pixels), None, 0)
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = self.gdi.SelectObject(dc, bitmap)

            def create(method, destructor, *arguments):
                result = ctypes.c_void_p()
                self._check(getattr(self.plus, method)(*arguments, ctypes.byref(result)))
                cleanup.append((destructor, result))
                return result

            # PARGB shares the top-down DIB buffer with the alpha compositor.
            image = create("GdipCreateBitmapFromScan0", "GdipDisposeImage",
                           width, height, width * 4, 0xE200B, pixels)
            graphics = create("GdipGetImageGraphicsContext", "GdipDeleteGraphics", image)
            clear = 0xFF000000 | int(background[1:], 16) if unlocked else 0
            self._check(self.plus.GdipGraphicsClear(graphics, clear))
            # Grayscale anti-aliasing keeps edge pixels valid on transparent surfaces.
            self._check(self.plus.GdipSetTextRenderingHint(graphics, 3))
            family = create("GdipCreateFontFamilyFromName", "GdipDeleteFontFamily", "Segoe UI", None)
            font = create("GdipCreateFont", "GdipDeleteFont", family, 13 * scale, 0, 2)
            formatting = create("GdipStringFormatGetGenericTypographic", "GdipDeleteStringFormat")
            self._check(self.plus.GdipSetStringFormatAlign(formatting, 1))
            self._check(self.plus.GdipSetStringFormatLineAlign(formatting, 1))
            self._check(self.plus.GdipSetStringFormatFlags(formatting, 0x1000 | 0x4000))
            brush = create("GdipCreateSolidFill", "GdipDeleteBrush", 0xFF000000 | int(foreground[1:], 16))
            for text, left, extent in ((timer, 0, 178), (percent, 152, 62)):
                layout = RectF(left * scale, 0, extent * scale, height)
                self._check(self.plus.GdipDrawString(graphics, text, -1, font,
                                                    ctypes.byref(layout), formatting, brush))
            if stale:
                dot = create("GdipCreateSolidFill", "GdipDeleteBrush", 0xFFD4A266)
                self._check(self.plus.GdipFillEllipse(graphics, dot, 4*scale, 13*scale, 4*scale, 4*scale))
            self._check(self.plus.GdipFlush(graphics, 1))
            if preview is not None:
                png_encoder = ctypes.create_string_buffer(bytes.fromhex("06f47c55041ad3119a730000f81ef32e"))
                self._check(self.plus.GdipSaveImageToFile(image, str(preview), png_encoder, None))
            source = wintypes.POINT(0, 0)
            size = wintypes.SIZE(width, height)
            blend = BlendFunction(0, 0, 255, 1)
            if not self.user.UpdateLayeredWindow(self.window, None, None, ctypes.byref(size),
                                                 dc, ctypes.byref(source), 0, ctypes.byref(blend), 2):
                raise ctypes.WinError(ctypes.get_last_error())
            self.cache = key
        finally:
            for destructor, resource in reversed(cleanup):
                getattr(self.plus, destructor)(resource)
            if previous:
                self.gdi.SelectObject(dc, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(dc)

    def close(self):
        if self.token.value:
            self.plus.GdiplusShutdown(self.token)
            self.token.value = 0
