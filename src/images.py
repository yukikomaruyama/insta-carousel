"""Notion のページに置かれた画像を読み込み、確かめ、投稿用に整える。"""
from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

from . import notion

# Instagram が受け付ける縦横比(横÷縦)。4:5 の縦長 〜 1.91:1 の横長。
RATIO_MIN, RATIO_MAX = 0.8, 1.91
MAX_WIDTH = 1440
MAX_BYTES = 8 * 1024 * 1024


def download(url: str) -> Image.Image:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    try:
        img = Image.open(io.BytesIO(r.content))
        img.load()
    except Exception:
        raise ValueError("読み込めない画像があります。PNG か JPEG の画像を入れてください。")
    return ImageOps.exif_transpose(img)  # スマホ写真の向きを正す


def load(page_id: str) -> list[Image.Image]:
    return [download(u) for u in notion.image_urls(page_id)]


def check(images: list[Image.Image]) -> tuple[list[str], list[str]]:
    """(投稿できない理由, 知らせておきたい注意) を返す。"""
    errors, warnings = [], []
    if not images:
        return ["ページに画像がありません。画像を入れてから、もう一度「キャプション依頼」にしてください。"], []
    if len(images) > 10:
        errors.append(f"画像が{len(images)}枚あります。1回の投稿は10枚までです。")
    first = images[0].width / images[0].height
    for i, img in enumerate(images, start=1):
        ratio = img.width / img.height
        if ratio < RATIO_MIN - 0.01 or ratio > RATIO_MAX + 0.01:
            errors.append(
                f"{i}枚目の縦横比({img.width}×{img.height})は投稿できません。"
                "縦長は4:5(例 1080×1350)まで、横長は1.91:1までです。"
            )
        elif abs(ratio - first) > 0.01:
            warnings.append(f"{i}枚目の縦横比が1枚目と違います。投稿時に1枚目に合わせて切り取られます。")
        if img.width < 1080:
            warnings.append(f"{i}枚目は横{img.width}pxで小さめです。1080px以上だときれいに出ます。")
    return errors, warnings


def _flatten(img: Image.Image) -> Image.Image:
    """透明な部分は白にして、JPEG にできる形にする。"""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, "white")
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    return img.convert("RGB")


def save_jpeg(img: Image.Image, path: Path) -> None:
    """Instagram API が受け付けるのは JPEG だけなので、PNG などは変換する。"""
    out = _flatten(img)
    if out.width > MAX_WIDTH:
        out = out.resize((MAX_WIDTH, round(out.height * MAX_WIDTH / out.width)), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    for quality in (92, 88, 82, 75):
        out.save(path, "JPEG", quality=quality, optimize=True, progressive=False)
        if path.stat().st_size <= MAX_BYTES:
            return
    raise ValueError(f"画像が大きすぎます: {path.name}")


def for_claude(img: Image.Image) -> dict:
    """キャプションを書く AI に見せるための、小さめの画像データ。"""
    small = _flatten(img)
    small.thumbnail((1568, 1568), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def base_url() -> str:
    explicit = os.environ.get("IMAGE_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    branch = os.environ.get("GITHUB_REF_NAME", "main").strip()
    if not repo:
        raise SystemExit("画像の公開先がわかりません。IMAGE_BASE_URL を設定してください。")
    return f"https://raw.githubusercontent.com/{repo}/{branch}"


def wait_public(url: str, seconds: int = 180) -> None:
    """push 直後は反映に少しかかるので、見えるようになるまで待つ。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=15).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    raise RuntimeError(
        f"画像が公開URLで見えません: {url} "
        "リポジトリが公開(Public)になっているか確認してください。"
    )
