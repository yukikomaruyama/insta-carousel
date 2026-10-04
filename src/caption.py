"""① キャプションづくり。

Notion で ステータスが「キャプション依頼」または「作り直し」の行を拾い、
ページ内の画像を AI に見せて、画像に合うキャプションを書かせる。

    python -m src.caption
"""
from __future__ import annotations

import traceback

import anthropic

from . import images, notion
from .common import CONFIG, ROOT, require

TOOL = {
    "name": "save_caption",
    "description": "Instagram投稿のキャプションを保存する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "caption": {"type": "string", "description": "投稿のキャプション。ハッシュタグ込み"},
            "fact_notes": {
                "type": "string",
                "description": "投稿前に本人が確認するためのメモ。画像から読み取った要点と、確認してほしい点を短く",
            },
        },
        "required": ["caption", "fact_notes"],
    },
}


def validate(data: dict) -> None:
    caption = data.get("caption", "").strip()
    if not caption:
        raise ValueError("キャプションが空です")
    if len(caption) > 2200:
        raise ValueError("キャプションが2200文字を超えています")
    if caption.count("#") > 5:
        raise ValueError("ハッシュタグは5個までです")


def write_caption(theme: str, memo: str, pics: list, previous: str = "") -> dict:
    voice = (ROOT / "voice.md").read_text(encoding="utf-8")
    system = (
        "あなたはInstagramのキャプションを書く編集者です。"
        "添付の画像(投稿される順番どおり)を読み取り、その内容に合うキャプションを書きます。"
        "次のルールを必ず守ってください。\n\n" + voice
    )
    text = f"この画像{len(pics)}枚で投稿します。\n投稿のテーマ: {theme or '(指定なし。画像から読み取ってください)'}"
    if memo:
        text += f"\n\n本人からの補足・指示:\n{memo}"
    if previous:
        text += f"\n\n前回のキャプション(指示に沿って書き直してください):\n{previous}"
    content = [images.for_claude(p) for p in pics] + [{"type": "text", "text": text}]

    client = anthropic.Anthropic(api_key=require("ANTHROPIC_API_KEY"))
    messages = [{"role": "user", "content": content}]
    last_error = None
    for _ in range(3):
        resp = client.messages.create(
            model=CONFIG["claude_model"],
            max_tokens=2000,
            system=system,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "save_caption"},
            messages=messages,
        )
        block = next(b for b in resp.content if b.type == "tool_use")
        try:
            validate(block.input)
            return block.input
        except ValueError as e:
            # ルール違反があれば、理由を伝えて書き直してもらう
            last_error = e
            messages = messages + [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"保存できませんでした: {e}。直してもう一度保存してください。",
                }]},
            ]
    raise ValueError(f"キャプションがルールを満たしませんでした: {last_error}")


def main() -> None:
    redo = notion.query("作り直し", 10)
    redo_ids = {p["id"] for p in redo}
    pages = (redo + notion.query("キャプション依頼", 10))[: CONFIG["captions_per_run"]]
    if not pages:
        print("キャプションを作る行はありませんでした。")
        return
    for page in pages:
        page_id = page["id"]
        theme = notion.text_of(page, "テーマ")
        print(f"キャプションを作成: {theme or page_id}")
        try:
            pics = images.load(page_id)
            errors, warnings = images.check(pics)
            if errors:
                notion.report_error(page_id, "\n".join(errors))
                continue
            previous = notion.text_of(page, "キャプション") if page_id in redo_ids else ""
            data = write_caption(theme, notion.text_of(page, "メモ"), pics, previous)
            note = "\n".join([f"⚠️ {w}" for w in warnings] + [data["fact_notes"].strip()])
            notion.update(page_id, status="確認待ち", caption=data["caption"].strip(), note=note)
        except Exception as e:
            traceback.print_exc()
            notion.report_error(page_id, f"キャプションづくりに失敗しました: {e}")


if __name__ == "__main__":
    main()
