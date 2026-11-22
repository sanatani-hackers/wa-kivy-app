"""Module-level shims for Android compatibility.
Provides safe fallbacks for desktop-only / unbundled libraries (magic, PIL, httpx, linkpreview, tqdm)
so neonize can run in pure Python on Android without C-extension dependency issues.
"""
import sys
import types


def setup_shims():
    # 0. tqdm shim
    if "tqdm" not in sys.modules:
        tq = types.ModuleType("tqdm")

        class TqdmMock:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def update(self, *a):
                pass

            def close(self):
                pass

        tq.tqdm = TqdmMock
        sys.modules["tqdm"] = tq

    # 1. magic implementation (using real filetype library)
    if "magic" not in sys.modules:
        m = types.ModuleType("magic")

        try:
            import filetype
        except Exception:
            filetype = None

        class Magic:
            @staticmethod
            def from_buffer(buf, mime=True):
                if filetype is not None:
                    try:
                        kind = filetype.guess(buf)
                        if kind and kind.mime:
                            return kind.mime
                    except Exception:
                        pass
                if isinstance(buf, (bytes, bytearray)):
                    if buf.startswith(b"\xff\xd8\xff"): return "image/jpeg"
                    if buf.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
                    if buf.startswith(b"GIF8"): return "image/gif"
                    if buf.startswith(b"RIFF") and len(buf) >= 12 and buf[8:12] == b"WEBP": return "image/webp"
                    if buf.startswith(b"OggS"): return "audio/ogg"
                    if buf.startswith(b"%PDF"): return "application/pdf"
                    if buf.startswith(b"ID3") or buf.startswith(b"\xff\xfb") or buf.startswith(b"\xff\xf3"): return "audio/mpeg"
                    if len(buf) >= 8 and buf[4:8] in (b"ftyp", b"moov"): return "video/mp4"
                return "application/octet-stream"

            @staticmethod
            def from_file(path, mime=True):
                if filetype is not None:
                    try:
                        kind = filetype.guess(path)
                        if kind and kind.mime:
                            return kind.mime
                    except Exception:
                        pass
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

        class ImageFilterMod:
            BLUR = "BLUR"
            GaussianBlur = lambda *a, **kw: None

        pil.Image = ImageMod
        pil.ImageFilter = ImageFilterMod
        pil.ImageSequence = type("ImageSequence", (), {"Iterator": lambda *a: []})
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = ImageMod
        sys.modules["PIL.ImageFilter"] = ImageFilterMod
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

    # 5. Crypto shim (pycryptodome)
    if "Crypto" not in sys.modules:
        cr = types.ModuleType("Crypto")
        cip = types.ModuleType("Crypto.Cipher")
        ut = types.ModuleType("Crypto.Util")
        pad_mod = types.ModuleType("Crypto.Util.Padding")

        class _MockAES:
            MODE_CBC = 1
            MODE_EAX = 2
            @staticmethod
            def new(*a, **kw):
                return type("_AESInst", (), {"encrypt": lambda s, d: d, "decrypt": lambda s, d: d})()

        cip.AES = _MockAES
        def _pad(data, block_size=16):
            pad_len = block_size - (len(data) % block_size)
            return data + bytes([pad_len] * pad_len)

        def _unpad(data, block_size=16):
            if not data: return data
            pad_len = data[-1]
            if 1 <= pad_len <= block_size and data[-pad_len:] == bytes([pad_len] * pad_len):
                return data[:-pad_len]
            return data

        pad_mod.pad = _pad
        pad_mod.unpad = _unpad
        ut.Padding = pad_mod
        cr.Cipher = cip
        cr.Util = ut

        sys.modules["Crypto"] = cr
        sys.modules["Crypto.Cipher"] = cip
        sys.modules["Crypto.Util"] = ut
        sys.modules["Crypto.Util.Padding"] = pad_mod

    # 6. cryptography shim
    if "cryptography" not in sys.modules:
        cg = types.ModuleType("cryptography")
        haz = types.ModuleType("cryptography.hazmat")
        prim = types.ModuleType("cryptography.hazmat.primitives")
        hashes_mod = types.ModuleType("cryptography.hazmat.primitives.hashes")
        hashes_mod.SHA256 = type("SHA256", (), {})
        kdf_mod = types.ModuleType("cryptography.hazmat.primitives.kdf")
        hkdf_mod = types.ModuleType("cryptography.hazmat.primitives.kdf.hkdf")

        import hashlib, hmac, math
        class _RealHKDF:
            def __init__(self, algorithm=None, length=112, salt=None, info=b""):
                self.length = length
                self.salt = salt if salt else bytes([0] * 32)
                self.info = info if info else b""

            def derive(self, key):
                prk = hmac.new(self.salt, key, hashlib.sha256).digest()
                okm = b""
                t = b""
                for i in range(1, math.ceil(self.length / 32) + 1):
                    t = hmac.new(prk, t + self.info + bytes([i]), hashlib.sha256).digest()
                    okm += t
                return okm[:self.length]

        hkdf_mod.HKDF = _RealHKDF
        prim.hashes = hashes_mod
        prim.kdf = kdf_mod
        kdf_mod.hkdf = hkdf_mod
        haz.primitives = prim
        cg.hazmat = haz

        sys.modules["cryptography"] = cg
        sys.modules["cryptography.hazmat"] = haz
        sys.modules["cryptography.hazmat.primitives"] = prim
        sys.modules["cryptography.hazmat.primitives.hashes"] = hashes_mod
        sys.modules["cryptography.hazmat.primitives.kdf"] = kdf_mod
        sys.modules["cryptography.hazmat.primitives.kdf.hkdf"] = hkdf_mod


# Apply automatically upon import
setup_shims()
