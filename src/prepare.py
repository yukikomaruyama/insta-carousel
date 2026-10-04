"""② 投稿の準備。承認済みの1本の画像を、投稿用の JPEG に整えて slides/ に置く。

このあと画像を GitHub に push し、src.publish が投稿する。

    python -m src.prepare
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import images, notion
from .common import ROOT

PENDING = ROOT / "pending.json"


def main() -> None:
    PENDING.write_text("{}", encoding="utf-8")

    # 前回の投稿が途中で止まっていたら、二重投稿を避けるため何もしない
    if notion.query("投稿中", 1):
        raise SystemExit(
            "ステータスが「投稿中」のままの行があります。Instagram に投稿されているか確認し、"
            "投稿済みなら「投稿済み」に、されていなければ「承認」に戻してください。"
        )

    pages = notion.query("承認", 1)
    if not pages:
        print("承認済みの投稿がありません。今回は何も投稿しません。")
        return
    page = pages[0]
    page_id = page["id"]
    print(f"投稿対象: {notion.text_of(page, 'テーマ') or page_id}")

    try:
        pics = images.load(page_id)
        errors, _ = images.check(pics)
        if errors:
            raise ValueError("\n".join(errors))
        folder = Path("slides") / page_id.replace("-", "") / time.strftime("%Y%m%d-%H%M%S")
        files = []
        for i, pic in enumerate(pics, start=1):
            path = folder / f"{i:02d}.jpg"
            images.save_jpeg(pic, ROOT / path)
            files.append(path.as_posix())
    except Exception as e:
        notion.report_error(page_id, f"投稿の準備に失敗しました: {e}")
        raise SystemExit(f"投稿の準備に失敗しました: {e}")

    PENDING.write_text(json.dumps({"page_id": page_id, "files": files}, ensure_ascii=False), encoding="utf-8")
    print(f"画像 {len(files)} 枚を整えました。")


if __name__ == "__main__":
    main()
