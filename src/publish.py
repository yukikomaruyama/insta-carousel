"""③ 投稿。src.prepare が整えた1本を Instagram に投稿する。

    python -m src.publish            # 本番
    DRY_RUN=1 python -m src.publish  # 何が投稿されるか表示するだけ
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

from . import images, instagram, notion
from .common import ROOT

JST = timezone(timedelta(hours=9))
PENDING = ROOT / "pending.json"


def main() -> None:
    dry = os.environ.get("DRY_RUN", "").strip() not in ("", "0", "false")
    job = json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else {}
    if not job:
        print("投稿するものがありません。")
        return
    page_id, files = job["page_id"], job["files"]

    # 準備のあとで承認が取り消されていないか、もう一度確かめる
    page = next((p for p in notion.query("承認", 10) if p["id"] == page_id), None)
    if page is None:
        print("この投稿は「承認」ではなくなっています。投稿しません。")
        return
    theme = notion.text_of(page, "テーマ")
    caption = notion.text_of(page, "キャプション")

    print(f"投稿対象: {theme or page_id}")
    print(f"画像 {len(files)} 枚 / キャプション {len(caption)} 文字")
    if dry:
        for f in files:
            print("  ", f)
        print(caption)
        print("(確認だけのモードなので、投稿していません)")
        return

    try:
        if not caption:
            raise ValueError("キャプションが空です")
        if len(caption) > 2200:
            raise ValueError("キャプションが2200文字を超えています")
        urls = [f"{images.base_url()}/{f}" for f in files]
        images.wait_public(urls[-1])
        notion.update(page_id, status="投稿中")
        result = instagram.publish(urls, caption)
    except Exception as e:
        notion.report_error(
            page_id,
            f"投稿に失敗しました: {e}\nInstagram に出ていないことを確認してから「承認」に戻してください。",
        )
        raise SystemExit(f"投稿に失敗しました: {e}")

    # ここまで来たら Instagram には出ている。Notion の更新は粘り強くやり直す。
    today = datetime.now(JST).strftime("%Y-%m-%d")
    for attempt in range(5):
        try:
            notion.update(page_id, status="投稿済み", posted_on=today, url=result["permalink"] or None)
            break
        except Exception as e:
            print(f"Notion の更新に失敗({attempt + 1}/5): {e}")
            time.sleep(10)
    else:
        raise SystemExit(
            f"投稿は成功しましたが、Notion を更新できませんでした。「{theme}」を手動で「投稿済み」にしてください。"
        )
    PENDING.unlink(missing_ok=True)
    print(f"投稿しました: {result['permalink'] or result['id']}")


if __name__ == "__main__":
    main()
