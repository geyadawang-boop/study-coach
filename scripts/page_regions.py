#!/usr/bin/env python3
"""Turn model-read page regions into per-question crops and text.

Multi-column and cross-page papers cannot be split by "next number on a new
line": the number may sit in another column, and one question may continue on
the next page. The model looks at the rendered page image and writes a
regions.json; this script validates that geometry, renders every region to
`fig/`, and extracts text per region (text layer first, OCR only with --ocr).
Downstream, the model fills type/points/concepts and `intake_check.py` gates
the result. No full-page reflow is attempted.

regions.json (model-authored):
  {
    "schema": 1,
    "paper": "MID-2025A",
    "unit": "norm",                  # or "px"
    "page_size": {"1": [1190, 1684]},# required when unit=px
    "regions": [
      {"no": "1", "page": 1, "bbox": [0.08, 0.10, 0.92, 0.32]},
      {"no": "3", "page": 2, "bbox": [0.08, 0.10, 0.92, 0.60],
       "page_end": 3, "bbox_end": [0.08, 0.10, 0.92, 0.35]}
    ]
  }

bbox = [x0, y0, x1, y1], origin top-left. For a cross-page question give both
`page_end` and `bbox_end`; the two crops are listed in `images` in order.

Usage:
  # 1) render pages and emit a template for the model to fill
  python page_regions.py paper.pdf --template --pages 2-3 --render fig/pages \
      --out fig/regions.template.json
  # 1b) let the script propose regions from text-layer/OCR line boxes
  python page_regions.py paper.pdf --suggest --pages 30 --out fig/regions.suggested.json
  python page_regions.py photo.jpg --suggest --ocr --out fig/regions.suggested.json
  # 2) validate the filled regions, crop to fig/, extract text per region
  python page_regions.py paper.pdf --regions fig/regions.json --fig fig \
      --out fig/questions.regions.jsonl
  # photos: same flow with page=1; --ocr is required to get text
  python page_regions.py photo.jpg --regions fig/regions.json --ocr --fig fig

Exit codes: 0 = rows built; 1 = --suggest found no question number;
2 = unreadable input or invalid geometry; 3 = missing dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

LOW_CONF = 0.90
SAFE = re.compile(r"[^\w\-]+")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
NUMBER_DOT = re.compile(r"^(\d{1,3})\s*[.、．](?!\d)")
NUMBER_DI = re.compile(r"^第\s*(\d{1,3})\s*题")
MAX_NUMBER = 200
PAD = 0.005


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    if not pages:
        raise ValueError("--pages 为空")
    return sorted(set(pages))


def safe_no(value: str) -> str:
    return SAFE.sub("_", value).strip("_") or "x"


def as_bbox(value) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox 必须是 4 个数 [x0,y0,x1,y1]")
    out = [float(v) for v in value]
    if not all(0.0 <= v <= 1.0 for v in out):
        raise ValueError("bbox 归一化坐标必须在 0–1；像素坐标请写 unit=px 并给 page_size")
    if out[0] >= out[2] or out[1] >= out[3]:
        raise ValueError("bbox 需满足 x0<x1 且 y0<y1")
    return out


def to_norm(bbox: list[float], unit: str, size: tuple[float, float] | None,
            page: int) -> list[float]:
    if unit == "norm":
        return as_bbox(bbox)
    if size is None:
        raise ValueError(f"unit=px 时缺 page_size[{page}]")
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError(f"page_size[{page}] 必须为正数")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return as_bbox([x0 / width, y0 / height, x1 / width, y1 / height])


def load_regions(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        data = {"regions": data}
    if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
        raise ValueError("regions.json 需要 regions 数组")
    return data


def page_size(data: dict, page: int) -> tuple[float, float] | None:
    sizes = data.get("page_size") or {}
    value = sizes.get(str(page)) if isinstance(sizes, dict) else None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    if isinstance(data.get("width"), (int, float)) and isinstance(data.get("height"), (int, float)):
        return float(data["width"]), float(data["height"])
    return None


def overlap_ratio(a: list[float], b: list[float]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller else 0.0


def render_pages(doc, pages: list[int], dpi: int, folder: Path) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for page_no in pages:
        pix = doc.load_page(page_no - 1).get_pixmap(dpi=dpi)
        target = folder / f"P{page_no:03d}.png"
        pix.save(target)
        rows.append({"page": page_no, "width": pix.width, "height": pix.height,
                     "image": target.as_posix()})
    return rows


def crop(doc, page_no: int, bbox: list[float], dpi: int, target: Path) -> None:
    page = doc.load_page(page_no - 1)
    rect = page.rect
    clip = (rect.x0 + bbox[0] * rect.width, rect.y0 + bbox[1] * rect.height,
            rect.x0 + bbox[2] * rect.width, rect.y0 + bbox[3] * rect.height)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    target.parent.mkdir(parents=True, exist_ok=True)
    pix.save(target)


def crop_image(image, bbox: list[float], target: Path) -> None:
    width, height = image.size
    box = (round(bbox[0] * width), round(bbox[1] * height),
           round(bbox[2] * width), round(bbox[3] * height))
    target.parent.mkdir(parents=True, exist_ok=True)
    image.crop(box).save(target)


def text_of(doc, page_no: int, bbox: list[float]) -> str:
    page = doc.load_page(page_no - 1)
    rect = page.rect
    clip = (rect.x0 + bbox[0] * rect.width, rect.y0 + bbox[1] * rect.height,
            rect.x0 + bbox[2] * rect.width, rect.y0 + bbox[3] * rect.height)
    return page.get_text("text", clip=clip).strip()


def ocr_of(image: Path) -> tuple[str, float]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ocr_page import run_ocr

    lines = run_ocr(image)
    text = "\n".join(line["text"] for line in lines if line["text"]).strip()
    conf = min((line["score"] for line in lines), default=0.0)
    return text, round(conf, 4)


def _line(text: str, bbox, page: int, width: float, height: float) -> dict:
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return {
        "page": page,
        "text": " ".join(str(text).split()),
        "bbox": [
            max(0.0, min(1.0, x0 / width)), max(0.0, min(1.0, y0 / height)),
            max(0.0, min(1.0, x1 / width)), max(0.0, min(1.0, y1 / height)),
        ],
    }


def text_layer_lines(doc, page_no: int) -> list[dict]:
    page = doc.load_page(page_no - 1)
    width, height = page.rect.width, page.rect.height
    rows: list[dict] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if text:
                rows.append(_line(text, line["bbox"], page_no, width, height))
    return rows


def ocr_image_lines(image: Path, page: int) -> list[dict]:
    from PIL import Image

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ocr_page import run_ocr_lines

    with Image.open(image) as handle:
        width, height = handle.size
    return [_line(row["text"], row["box"], page, width, height)
            for row in run_ocr_lines(image) if row["text"]]


def split_columns(lines: list[dict], depth: int = 0) -> list[list[dict]]:
    """Recursive XY cut: split on a vertical gap, keep column reading order."""
    ordered = sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))
    if len(lines) < 4 or depth >= 3:
        return [ordered]
    left = min(line["bbox"][0] for line in lines)
    right = max(line["bbox"][2] for line in lines)
    span = right - left
    if span < 0.30:
        return [ordered]
    bins = 240
    cover = [0] * bins
    for line in lines:
        if line["bbox"][2] - line["bbox"][0] > 0.72 * span:
            continue  # 通栏标题不参与分栏，避免挡住栏间空隙
        a = int((line["bbox"][0] - left) / span * (bins - 1))
        b = int((line["bbox"][2] - left) / span * (bins - 1))
        for index in range(max(a, 0), min(b, bins - 1) + 1):
            cover[index] = 1
    best_cut, best_len = 0.0, 0
    start = None
    for index, value in enumerate(cover + [1]):
        if value == 0 and start is None:
            start = index
        elif value == 1 and start is not None:
            length = index - start
            lo = left + start / (bins - 1) * span
            hi = left + index / (bins - 1) * span
            if (length > best_len and length >= 8
                    and lo > left + 0.12 * span and hi < right - 0.12 * span):
                best_cut, best_len = (lo + hi) / 2, length
            start = None
    if not best_cut:
        return [ordered]
    left_lines = [line for line in lines
                  if (line["bbox"][0] + line["bbox"][2]) / 2 < best_cut]
    right_lines = [line for line in lines
                   if (line["bbox"][0] + line["bbox"][2]) / 2 >= best_cut]
    if len(left_lines) < 2 or len(right_lines) < 2:
        return [ordered]
    return split_columns(left_lines, depth + 1) + split_columns(right_lines, depth + 1)


def question_no(text: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", text).strip()
    match = NUMBER_DOT.match(normalized) or NUMBER_DI.match(normalized)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= MAX_NUMBER else None


def increasing_runs(items: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Split starts into runs whose numbers strictly increase.

    A paper often restarts numbering per 大题; keeping only the longest run
    would silently drop every later section, so callers get all runs.
    """
    runs: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for item in items:
        if current and item[1] <= current[-1][1]:
            runs.append(current)
            current = []
        current.append(item)
    if current:
        runs.append(current)
    return runs


def _ocr_gap(image: Path, box: tuple[int, int, int, int], scale: int = 3) -> list[dict]:
    from PIL import Image

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ocr_page import run_ocr_lines

    with Image.open(image) as handle:
        crop = handle.crop(box)
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    target = Path(tempfile.gettempdir()) / f"page-regions-gap-{abs(hash(box)) % 10**8}.png"
    crop.save(target)
    try:
        return run_ocr_lines(target)
    finally:
        target.unlink(missing_ok=True)


def recover_gaps(image: Path, group: list[dict]) -> list[dict]:
    """OCR the left strip of a suspicious vertical gap.

    A line with a stacked fraction can make the recognizer drop the whole line
    (p30: "34. 证明：f(x)=1/x ..." came back as "C"). The number itself sits in
    the left margin and survives a narrow crop, so gaps wider than normal line
    spacing get a second targeted OCR pass.
    """
    if len(group) < 3:
        return []
    from PIL import Image

    with Image.open(image) as handle:
        width, height = handle.size
    heights = sorted(line["bbox"][3] - line["bbox"][1] for line in group)
    steps = sorted(max(0.0, group[i + 1]["bbox"][1] - group[i]["bbox"][3])
                   for i in range(len(group) - 1))
    median_h = heights[len(heights) // 2]
    median_step = steps[len(steps) // 2]
    limit = max(0.020, 2.5 * median_step, 1.15 * median_h)
    left = min(line["bbox"][0] for line in group)
    right = max(line["bbox"][2] for line in group)
    probe_right = min(1.0, left + 0.32 * (right - left))

    def probe(y0: float, y1: float) -> int | None:
        box = (max(0, int(left * width) - 2), max(0, int(y0 * height)),
               min(width, int(probe_right * width)), min(height, int(y1 * height)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        try:
            rows = _ocr_gap(image, box)
        except ImportError:
            raise
        except Exception:
            return None
        return next((value for row in rows
                     if (value := question_no(row["text"]))), None)

    recovered: list[dict] = []
    # 1) 整行被识别成 1–2 个乱码字符（公式分式最常见），按行左条重取题号
    for index, line in enumerate(group):
        if len(line["text"].strip()) > 2:
            continue
        top = group[index - 1]["bbox"][3] + 0.002 if index else 0.0
        bottom = group[index + 1]["bbox"][1] - 0.002 if index + 1 < len(group) else 1.0
        y0 = max(top, line["bbox"][1] - 1.6 * median_h)
        y1 = min(bottom, line["bbox"][3] + 0.8 * median_h)
        if y1 <= y0:
            continue
        number = probe(y0, y1)
        if not number:
            continue
        line["text"] = f"{number}.（乱码行回填）"
        line["bbox"] = [left, y0, right, y1]
        line["recovered"] = True  # 已就地替换，with_gap_recovery 不要再追加一次
    # 2) 整行漏检：行间空隙明显大于常规行距时，按空隙左条重取题号
    for previous, following in zip(group, group[1:]):
        gap = following["bbox"][1] - previous["bbox"][3]
        if gap <= limit or gap > 0.30:
            continue
        y0 = previous["bbox"][3] + 0.002
        y1 = following["bbox"][1] - 0.002
        number = probe(y0, y1)
        if not number:
            continue
        recovered.append({
            "page": previous["page"],
            "text": f"{number}.（漏检行回填）",
            "bbox": [left, y0, right, y1],
            "recovered": True,
        })
    return recovered


def with_gap_recovery(image: Path, lines: list[dict]) -> list[dict]:
    out: list[dict] = []
    for group in split_columns(lines):
        out.extend(group)
        out.extend(recover_gaps(image, group))
    return out


def union_box(lines: list[dict]) -> list[float]:
    return [
        min(line["bbox"][0] for line in lines), min(line["bbox"][1] for line in lines),
        max(line["bbox"][2] for line in lines), max(line["bbox"][3] for line in lines),
    ]


def padded(box: list[float]) -> list[float]:
    return [round(max(0.0, box[0] - PAD), 6), round(max(0.0, box[1] - PAD), 6),
            round(min(1.0, box[2] + PAD), 6), round(min(1.0, box[3] + PAD), 6)]


def suggest_regions(pages: list[tuple[int, list[dict]]]) -> tuple[list[dict], list[str]]:
    flat: list[dict] = []
    for _, lines in pages:
        for group in split_columns(lines):
            flat.extend(group)
    starts: list[tuple[int, int]] = []
    runs = increasing_runs(
        [(index, number) for index, line in enumerate(flat)
         if (number := question_no(line["text"]))]
    )
    warnings: list[str] = []
    if len(runs) > 1:
        warnings.append(
            f"题号出现 {len(runs)} 段回退（多为按大题重新编号），已分段提案，请逐段复核"
        )
    for run in runs:
        starts.extend(run)
    if not starts:
        return [], ["未识别到题号"]
    regions: list[dict] = []
    for position, (start, number) in enumerate(starts):
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(flat) - 1
        block = flat[start:end + 1]
        while (len(block) > 1 and block[-1]["bbox"][1] >= 0.92
               and len(block[-1]["text"]) <= 30):
            block = block[:-1]  # 末题不带页脚页码
        first_page, last_page = block[0]["page"], block[-1]["page"]
        head = [line for line in block if line["page"] == first_page]
        item = {
            "no": str(number),
            "page": first_page,
            "bbox": padded(union_box(head)),
            "note": " ".join(line["text"] for line in block[:2])[:40],
        }
        if last_page > first_page:
            tail = [line for line in block if line["page"] == last_page]
            item["page_end"] = last_page
            item["bbox_end"] = padded(union_box(tail))
            if last_page - first_page > 1:
                warnings.append(
                    f"题 {number} 跨 {last_page - first_page + 1} 页，中间页将按整页裁图拼接，请复核"
                )
        regions.append(item)
    return regions, warnings


def validate(data: dict, page_count: int) -> tuple[list[dict], list[str]]:
    unit = str(data.get("unit") or "norm").lower()
    if unit not in {"norm", "px"}:
        raise ValueError("unit 只能是 norm 或 px")
    regions: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}
    seen_slug: dict[str, str] = {}
    for index, item in enumerate(data["regions"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个 region 不是对象")
        no = str(item.get("no") or "").strip()
        if not no:
            raise ValueError(f"第 {index} 个 region 缺 no")
        if no in seen:
            raise ValueError(f"题号重复：{no}（第 {seen[no]} 与第 {index} 个 region）")
        seen[no] = index
        slug = safe_no(no)
        if slug in seen_slug:
            raise ValueError(
                f"题号 {seen_slug[slug]} 与 {no} 映射到同一裁图文件名 Q{slug}，请改题号"
            )
        seen_slug[slug] = no
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            raise ValueError(f"区域 {no} 的 page 不是整数") from None
        if page < 1 or page > page_count:
            raise ValueError(f"区域 {no} 的 page={page} 超出 1..{page_count}")
        bbox = to_norm(item.get("bbox"), unit, page_size(data, page), page)
        end = item.get("page_end")
        bbox_end = None
        if end is not None or item.get("bbox_end") is not None:
            if end is None or item.get("bbox_end") is None:
                raise ValueError(f"区域 {no} 跨页需同时给 page_end 与 bbox_end")
            end = int(end)
            if end < page or end > page_count:
                raise ValueError(f"区域 {no} 的 page_end={end} 不在 {page}..{page_count}")
            bbox_end = to_norm(item.get("bbox_end"), unit, page_size(data, end), end)
            if end == page:
                warnings.append(f"区域 {no} 的 page_end 与 page 相同，已按单页处理")
                bbox_end = None
            elif end - page > 1:
                warnings.append(
                    f"区域 {no} 跨 {end - page + 1} 页，中间 {end - page - 1} 页按整页裁图拼接，请复核"
                )
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area > 0.92:
            warnings.append(f"区域 {no} 占整页 {area:.0%}，若含多题请拆细")
        regions.append({
            "no": no,
            "page": page,
            "page_end": end if bbox_end else page,
            "bbox": bbox,
            "bbox_end": bbox_end,
            "note": str(item.get("note") or ""),
        })

    for i, left in enumerate(regions):
        for right in regions[i + 1:]:
            if left["page"] == right["page"]:
                ratio = overlap_ratio(left["bbox"], right["bbox"])
                if ratio > 0.60:
                    warnings.append(
                        f"区域 {left['no']} 与 {right['no']} 在第 {left['page']} 页重叠 {ratio:.0%}"
                    )
    return regions, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="PDF（或单页图片）")
    parser.add_argument("--template", action="store_true", help="只渲染页面并输出 regions 模板")
    parser.add_argument("--suggest", action="store_true",
                        help="按题号与行框自动提案 regions.json（文本层优先，--ocr 时用 OCR 行框）")
    parser.add_argument("--pages", default="1", help="模板/提案模式要处理的页，如 2-4,7")
    parser.add_argument("--regions", help="模型填写好的 regions.json")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--render", default="fig/pages", help="模板模式渲染目录")
    parser.add_argument("--fig", default="fig", help="裁图输出目录")
    parser.add_argument("--ocr", action="store_true", help="文本层不足时对裁图跑 OCR")
    parser.add_argument("--min-chars", type=int, default=4, help="文本层少于此字数视为不足")
    parser.add_argument("--out", help="输出 JSONL 路径（默认打印到标准输出）")
    parser.add_argument("--json", action="store_true", help="输出单个 JSON 对象")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    if not args.template and not args.suggest and not args.regions:
        print("需要 --template、--suggest 或 --regions 之一", file=sys.stderr)
        return 2

    is_image = path.suffix.lower() in IMAGE_SUFFIXES
    if not is_image and path.suffix.lower() != ".pdf":
        print("只支持 PDF 或常见图片（jpg/png/webp/bmp/tif）", file=sys.stderr)
        return 2

    doc = None
    image = None
    if is_image:
        try:
            from PIL import Image, ImageOps
            image = ImageOps.exif_transpose(Image.open(path))  # 手机照片按 EXIF 摆正后再定尺寸/裁图
            page_count = 1
        except ImportError:
            print("处理图片需要 Pillow：pip install pillow", file=sys.stderr)
            return 3
        except Exception as exc:
            print(f"无法打开图片（{type(exc).__name__}）：{exc}", file=sys.stderr)
            return 2
    else:
        try:
            from fitz_compat import fitz
        except ImportError:
            print("需要 PyMuPDF：pip install pymupdf", file=sys.stderr)
            return 3
        try:
            doc = fitz.open(path)
        except Exception as exc:
            print(f"无法打开 PDF（{type(exc).__name__}）：{exc}", file=sys.stderr)
            return 2
        page_count = doc.page_count

    from contextlib import nullcontext

    try:
        with (doc if doc is not None else nullcontext()):
            if args.template:
                try:
                    pages = parse_pages(args.pages)
                except ValueError as exc:
                    print(f"bad --pages：{exc}", file=sys.stderr)
                    return 2
                if is_image and pages != [1]:
                    print("图片只有第 1 页", file=sys.stderr)
                    return 2
                bad = [p for p in pages if p < 1 or p > page_count]
                if bad:
                    print(f"页码越界：{bad}，共 {page_count} 页", file=sys.stderr)
                    return 2
                if is_image:
                    rows = [{"page": 1, "width": image.width, "height": image.height,
                             "image": path.as_posix()}]
                else:
                    rows = render_pages(doc, pages, args.dpi, Path(args.render))
                data = {
                    "schema": 1,
                    "paper": path.stem,
                    "unit": "norm",
                    "dpi": args.dpi,
                    "bbox_hint": "看渲染图填 regions[]：no/page/bbox=[x0,y0,x1,y1]，0–1 归一化、左上原点；"
                                 "跨页题加 page_end 与 bbox_end；用像素坐标时改 unit=px 并写 page_size",
                    "pages": rows,
                    "page_size": {str(row["page"]): [row["width"], row["height"]] for row in rows},
                    "regions": [],
                }
                text = json.dumps(data, ensure_ascii=False, indent=2)
                if args.out:
                    target = Path(args.out)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text + "\n", encoding="utf-8")
                    print(f"wrote {target} pages={len(rows)}")
                else:
                    print(text)
                return 0

            if args.suggest:
                try:
                    pages = parse_pages(args.pages)
                except ValueError as exc:
                    print(f"bad --pages：{exc}", file=sys.stderr)
                    return 2
                if is_image and pages != [1]:
                    print("图片只有第 1 页", file=sys.stderr)
                    return 2
                bad = [p for p in pages if p < 1 or p > page_count]
                if bad:
                    print(f"页码越界：{bad}，共 {page_count} 页", file=sys.stderr)
                    return 2
                if is_image and not args.ocr:
                    print("图片没有文本层，--suggest 需要 --ocr", file=sys.stderr)
                    return 2
                collected: list[tuple[int, list[dict]]] = []
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        for page_no in pages:
                            if is_image:
                                normalized = Path(tmp) / "suggest-input.png"
                                image.save(normalized)  # OCR 与行框统一用摆正后的图
                                lines = with_gap_recovery(
                                    normalized, ocr_image_lines(normalized, 1)
                                )
                            else:
                                lines = text_layer_lines(doc, page_no)
                                chars = sum(len(line["text"]) for line in lines)
                                if args.ocr and chars < args.min_chars * 5:
                                    target = Path(tmp) / f"suggest-P{page_no:03d}.png"
                                    doc.load_page(page_no - 1).get_pixmap(dpi=args.dpi).save(target)
                                    lines = with_gap_recovery(
                                        target, ocr_image_lines(target, page_no)
                                    )
                                elif chars == 0:
                                    print(f"第 {page_no} 页没有文本层，扫描件请加 --ocr",
                                          file=sys.stderr)
                                    return 2
                            collected.append((page_no, lines))
                except ImportError:
                    print("需要 RapidOCR：pip install rapidocr-onnxruntime", file=sys.stderr)
                    return 3
                except Exception as exc:
                    print(f"--suggest 失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
                    return 2
                regions, warnings = suggest_regions(collected)
                if not regions:
                    print("未识别到题号；改用 --template 手填 bbox", file=sys.stderr)
                    return 1
                recovered = sum(1 for _, lines in collected for line in lines
                                if line.get("recovered"))
                data = {
                    "schema": 1,
                    "paper": path.stem,
                    "unit": "norm",
                    "suggested": True,
                    "recovered": recovered,
                    "pages": pages,
                    "bbox_hint": "脚本按题号与行框自动提案，请复核 bbox 后再用 --regions 裁图；"
                                 "图案、跨栏题与公式行需人工修正",
                    "regions": regions,
                }
                for warning in warnings:
                    print(f"warning: {warning}", file=sys.stderr)
                text = json.dumps(data, ensure_ascii=False, indent=2)
                cross = sum(1 for region in regions if region.get("page_end"))
                if args.out:
                    target = Path(args.out)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text + "\n", encoding="utf-8")
                    print(f"wrote {target} suggested={len(regions)} cross_page={cross} "
                          f"recovered={recovered}")
                else:
                    print(text)
                return 0

            try:
                data = load_regions(Path(args.regions))
                regions, warnings = validate(data, page_count)
            except Exception as exc:
                print(f"regions 无效（{type(exc).__name__}）：{exc}", file=sys.stderr)
                return 2
            if not regions:
                print("regions 为空：请先按 --template 填写 bbox，或用 --suggest 自动提案",
                      file=sys.stderr)
                return 1

            paper = str(data.get("paper") or path.stem)
            fig = Path(args.fig)
            rows: list[dict] = []
            pending = 0
            counts = {"text": 0, "ocr": 0, "empty": 0}
            for region in regions:
                no = region["no"]
                first = fig / f"P{region['page']:03d}-Q{safe_no(no)}.png"
                if is_image:
                    crop_image(image, region["bbox"], first)
                else:
                    crop(doc, region["page"], region["bbox"], args.dpi, first)
                images = [first.as_posix()]
                text = "" if is_image else text_of(doc, region["page"], region["bbox"])
                source = "regions-text"
                conf = 1.0
                flags: list[str] = []
                if region["bbox_end"]:
                    for middle in range(region["page"] + 1, region["page_end"]):
                        mid = fig / f"P{middle:03d}-Q{safe_no(no)}.png"
                        crop(doc, middle, [0.0, 0.0, 1.0, 1.0], args.dpi, mid)
                        images.append(mid.as_posix())
                        mid_text = text_of(doc, middle, [0.0, 0.0, 1.0, 1.0])
                        text = "\n".join(part for part in (text, mid_text) if part)
                    second = fig / f"P{region['page_end']:03d}-Q{safe_no(no)}.png"
                    crop(doc, region["page_end"], region["bbox_end"], args.dpi, second)
                    images.append(second.as_posix())
                    tail = text_of(doc, region["page_end"], region["bbox_end"])
                    text = "\n".join(part for part in (text, tail) if part)

                if len(text) < args.min_chars:
                    if args.ocr:
                        try:
                            text, conf = ocr_of(first)
                            source = "regions-ocr"
                        except ImportError:
                            print("需要 RapidOCR：pip install rapidocr-onnxruntime", file=sys.stderr)
                            return 3
                        except Exception as exc:
                            print(f"OCR 失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
                            return 3
                        if conf < LOW_CONF:
                            flags.append("待核")
                    if len(text) < args.min_chars:
                        source = "regions-empty"
                        conf = 0.0
                        flags.append("待核")

                flags = list(dict.fromkeys(flags))
                if flags:
                    pending += 1
                counts[source.split("-")[1]] += 1
                rows.append({
                    "paper": paper,
                    "no": no,
                    "page_start": region["page"],
                    "page_end": region["page_end"],
                    "bbox": region["bbox"],
                    "bbox_end": region["bbox_end"],
                    "images": images,
                    "stem": text,
                    "source": source,
                    "conf": conf,
                    "flags": flags,
                })

            summary = {
                "paper": paper,
                "regions": len(rows),
                "cropped": sum(len(row["images"]) for row in rows),
                "pending": pending,
                "text": counts["text"],
                "ocr": counts["ocr"],
                "empty": counts["empty"],
                "warnings": warnings,
            }
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)

            if args.out:
                target = Path(args.out)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8",
                )
                print(f"wrote {target} regions={summary['regions']} cropped={summary['cropped']} "
                      f"pending={pending} text={counts['text']} ocr={counts['ocr']} empty={counts['empty']}")
            elif args.json:
                print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
            else:
                print(f"regions={summary['regions']} cropped={summary['cropped']} pending={pending} "
                      f"text={counts['text']} ocr={counts['ocr']} empty={counts['empty']}")
                for row in rows:
                    print(json.dumps(row, ensure_ascii=False))
    finally:
        if image is not None:
            image.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
