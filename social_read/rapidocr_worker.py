"""Optional local OCR subprocess. Dependencies live in a separate environment."""
import contextlib
import json
import sys
import warnings


def recognize(path, tiled=True):
    from PIL import Image, ImageOps
    from rapidocr import RapidOCR
    import numpy as np
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(path) as opened:
        width, height = opened.size
        if width > 20000 or height > 30000 or width * height > 60000000:
            return {"status": "image_budget_exceeded"}
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    # Midpoint ownership removes overlap duplicates without deleting repeated text
    # at different positions. Keep the full short side instead of shrinking a strip.
    tile_height = min(1600, max(800, width * 2))
    offsets = [0]
    if tiled and height > tile_height * 1.5:
        offsets = list(range(0, max(1, height - tile_height + 1), tile_height - 160))
        if offsets[-1] + tile_height < height:
            offsets.append(height - tile_height)
    engine = RapidOCR(params={"Global.log_level":"critical", "EngineConfig.onnxruntime.intra_op_num_threads":2, "EngineConfig.onnxruntime.inter_op_num_threads":2})
    lines = []
    for idx, top in enumerate(offsets):
        bottom = min(height, top + tile_height) if len(offsets) > 1 else height
        result = engine(np.asarray(image.crop((0, top, width, bottom))))
        low = (top + min(height, offsets[idx-1] + tile_height)) / 2 if idx else 0
        high = (bottom + offsets[idx+1]) / 2 if idx+1 < len(offsets) else height
        if result.txts is None:
            continue
        for text, score, box in zip(result.txts, result.scores, result.boxes):
            points = [[float(x), float(y)+top] for x,y in box]
            cy = sum(p[1] for p in points) / 4
            if low <= cy < high:
                lines.append({"text":str(text),"confidence":round(float(score),4),"box":points})
    lines.sort(key=lambda r:(min(p[1] for p in r["box"]),min(p[0] for p in r["box"])))
    return {"status":"success" if lines else "no_text_detected", "lines":lines,
            "width":width,"height":height,"resized":False,"tiles":len(offsets),
            "box_format":"pixel_quadrilateral_top_left", "formula_semantics":False}


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(sys.stderr):
            value = recognize(sys.argv[1], "--untiled" not in sys.argv[2:])
        print(json.dumps(value, ensure_ascii=False))
    except Exception:
        print(json.dumps({"status":"ocr_unavailable"}))
        raise SystemExit(1)
