"""
builders package — video/shorts assembly.

Compatibility shim: MoviePy 1.x references PIL.Image.ANTIALIAS, which
Pillow 10+ removed (renamed to LANCZOS). Restore the alias here so every
builder import gets a working MoviePy without pinning Pillow back.
"""
try:
    import PIL.Image
    if not hasattr(PIL.Image, "ANTIALIAS"):
        PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
except Exception:   # Pillow absent — builders will fail later with clear errors
    pass
