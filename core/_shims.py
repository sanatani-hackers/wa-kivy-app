"""Module-level shims for Android compatibility.
Provides safe fallbacks for desktop-only / unbundled libraries (magic, PIL, httpx, linkpreview)
so neonize can run in pure Python on Android without C-extension dependency issues.
"""
import sys
import types


def setup_shims():
    # 1. magic shim (Android has no libmagic.so)
    if "magic" not in sys.modules:
        m = types.ModuleType("magic")

        class Magic:
            @staticmethod
            def from_buffer(buf, mime=True):
                if isinstance(buf, (bytes, bytearray)):
                    if buf.startswith(b"\xff\xd8\xff"):
                        return "image/jpeg"
                    if buf.startswith(b"\x89PNG\r\n\x1a\n"):
                        return "image/png"
                    if buf.startswith(b"GIF8"):
                        return "image/gif"
                    if buf.startswith(b"RIFF") and len(buf) >= 12 and buf[8:12] == b"WEBP":
                        return "image/webp"
                    if buf.startswith(b"OggS"):
                        return "audio/ogg"
                    if buf.startswith(b"%PDF"):
                        return "application/pdf"
                    if buf.startswith(b"ID3") or buf.startswith(b"\xff\xfb") or buf.startswith(b"\xff\xf3"):
                        return "audio/mpeg"
                    if len(buf) >= 8 and buf[4:8] in (b"ftyp", b"moov"):
                        return "video/mp4"
                return "application/octet-stream"

            @staticmethod
            def from_file(path, mime=True):
                return "application/octet-stream"

        m.from_buffer = Magic.from_buffer
        m.from_file = Magic.from_file
        m.MagicException = type("MagicException", (Exception,), {})
        sys.modules["magic"] = m

    # 2. linkpreview shim
    if "linkpreview" not in sys.modules:
        lp = types.ModuleType("linkpreview")

        class LinkPreview:
            title = ""
            description = ""
            image = ""
            absolute_image = ""

        lp.link_preview = lambda *a, **kw: LinkPreview()
        lp.Link = type("Link", (), {})
        lp.LinkGrabber = type("LinkGrabber", (), {"get_content": lambda *a, **kw: (None, "")})
        lp.LinkPreview = LinkPreview
        sys.modules["linkpreview"] = lp

        class _ExMod(types.ModuleType):
            def __getattr__(self, name):
                cls = type(name, (Exception,), {})
                setattr(self, name, cls)
                return cls

        sys.modules["linkpreview.exceptions"] = _ExMod("linkpreview.exceptions")

    # 3. PIL (Pillow) fallback
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")

        class ImgObj:
            size = (100, 100)

            def crop(self, *a, **kw):
                return self

            def resize(self, *a, **kw):
                return self

            def save(self, *a, **kw):
                pass

        class ImageMod:
            Image = ImgObj

            @staticmethod
            def open(*a, **kw):
                return ImgObj()

            @staticmethod
            def new(*a, **kw):
                return ImgObj()

        pil.Image = ImageMod
        pil.ImageSequence = type("ImageSequence", (), {"Iterator": lambda *a: []})
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = ImageMod
        sys.modules["PIL.ImageSequence"] = pil.ImageSequence

    # 4. httpx fallback
    if "httpx" not in sys.modules:
        hx = types.ModuleType("httpx")

        class _HxEx(Exception):
            pass

        hx.ConnectTimeout = type("ConnectTimeout", (_HxEx,), {})
        hx.HTTPStatusError = type("HTTPStatusError", (_HxEx,), {})

        class AsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, *a, **kw):
                class Resp:
                    status_code = 200
                    content = b""
                    text = ""

                return Resp()

        hx.AsyncClient = AsyncClient
        sys.modules["httpx"] = hx


# Apply automatically upon import
setup_shims()
