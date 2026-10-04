"""通信をにせものに差し替えて、キャプション作成から投稿までの流れを確かめる。

    python -m tests.test_flow
"""
import os
import shutil
import tempfile
import types
from pathlib import Path

from PIL import Image

os.environ.update(NOTION_TOKEN="x", IG_ACCESS_TOKEN="x", IG_USER_ID="123",
                  ANTHROPIC_API_KEY="x", IMAGE_BASE_URL="https://example.com/base")

from src import caption, common, images, instagram, notion, prepare, publish  # noqa: E402

# 本物のフォルダを汚さないよう、作業場所を一時フォルダに移す
tmp = Path(tempfile.mkdtemp())
(tmp / "voice.md").write_text((common.ROOT / "voice.md").read_text(encoding="utf-8"), encoding="utf-8")
for mod in (caption, prepare, publish):
    mod.ROOT = tmp
prepare.PENDING = publish.PENDING = tmp / "pending.json"

# ---------- 1. 画像の確認と変換 ----------
png = Image.new("RGBA", (1080, 1350), (200, 80, 60, 0))      # 透過PNG(4:5)
big = Image.new("RGB", (3000, 3750), "navy")                  # 大きすぎる画像
story = Image.new("RGB", (1080, 1920), "gray")                # 縦長すぎ(9:16)
square = Image.new("RGB", (1080, 1080), "green")              # 正方形

assert images.check([])[0], "画像なしはエラーのはず"
assert images.check([png, story])[0], "9:16 はエラーのはず"
errors, warnings = images.check([png, square])
assert not errors and warnings, "縦横比ちがいは注意だけのはず"
assert images.check([png] * 11)[0], "11枚はエラーのはず"
assert images.check([png, png]) == ([], [])

images.save_jpeg(png, tmp / "a.jpg")
a = Image.open(tmp / "a.jpg")
assert (a.format, a.mode, a.size) == ("JPEG", "RGB", (1080, 1350))
assert a.getpixel((5, 5))[0] > 250, "透明部分は白になるはず"
images.save_jpeg(big, tmp / "b.jpg")
assert Image.open(tmp / "b.jpg").size == (1440, 1800), "横1440pxに縮むはず"
assert images.for_claude(big)["source"]["media_type"] == "image/jpeg"

# ---------- にせの Notion / Instagram / AI ----------
calls = []
row = {"status": "キャプション依頼", "caption": "", "note": "", "pics": [png, png, png]}


def fake_notion(method, path, body=None):
    calls.append(("notion", method, path, body))
    if path.endswith("/query"):
        if body["filter"]["select"]["equals"] != row["status"]:
            return {"results": []}
        return {"results": [{"id": "page-1", "properties": {
            "テーマ": {"title": [{"plain_text": "音読のクセ"}]},
            "メモ": {"rich_text": [{"plain_text": "保存を促して"}]},
            "キャプション": {"rich_text": [{"plain_text": row["caption"]}]},
        }}]}
    if method == "GET" and "/children" in path:
        blocks = [{"id": "p", "type": "paragraph", "paragraph": {}}]
        blocks += [{"id": f"i{n}", "type": "image",
                    "image": {"type": "file", "file": {"url": f"https://notion.example/{n}.png"}}}
                   for n in range(len(row["pics"]))]
        return {"results": blocks, "has_more": False}
    if method == "PATCH" and path.startswith("/pages/"):
        props = body["properties"]
        if "ステータス" in props:
            row["status"] = props["ステータス"]["select"]["name"]
        if "キャプション" in props:
            row["caption"] = "".join(t["text"]["content"] for t in props["キャプション"]["rich_text"])
        if "確認メモ" in props:
            row["note"] = "".join(t["text"]["content"] for t in props["確認メモ"]["rich_text"])
    return {}


def fake_download(url):
    return row["pics"][int(url.rsplit("/", 1)[1].split(".")[0])]


ai_requests = []
ai_answers = [
    {"caption": "ハッシュタグ多すぎ #1 #2 #3 #4 #5 #6", "fact_notes": "x"},   # 1回目はルール違反
    {"caption": "読むのが遅いんは、才能のせいちゃうで。\n\n保存しといてな。\n\n#速読 #脳トレ", "fact_notes": "画像3枚の要点: 音読のクセ"},
]


class FakeMessages:
    def create(self, **kw):
        ai_requests.append(kw)
        block = types.SimpleNamespace(type="tool_use", id="t1", input=ai_answers[min(len(ai_requests), 2) - 1])
        return types.SimpleNamespace(content=[block])


caption.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: types.SimpleNamespace(messages=FakeMessages()))

ig_ids = {"n": 0}


def fake_ig(method, path, **params):
    calls.append(("ig", method, path, params))
    if method == "POST" and path.endswith("/media"):
        ig_ids["n"] += 1
        return {"id": f"c{ig_ids['n']}"}
    if method == "POST" and path.endswith("/media_publish"):
        return {"id": "m1"}
    if params.get("fields") == "status_code":
        return {"status_code": "FINISHED"}
    if params.get("fields") == "permalink":
        return {"permalink": "https://www.instagram.com/p/abc/"}
    return {}


notion._call = fake_notion
instagram._call = fake_ig
images.download = fake_download
images.wait_public = lambda url, seconds=180: None

# ---------- 2. キャプションづくり ----------
caption.main()
assert row["status"] == "確認待ち", row
assert row["caption"].startswith("読むのが遅いんは"), row["caption"]
assert len(ai_requests) == 2, "ルール違反のときは書き直させるはず"
sent = ai_requests[0]["messages"][0]["content"]
assert [c["type"] for c in sent] == ["image", "image", "image", "text"]
assert "音読のクセ" in sent[-1]["text"] and "保存を促して" in sent[-1]["text"]

# 縦長すぎる画像が混ざっていたら、AI を呼ばずエラーにして理由を書く
row.update(status="キャプション依頼", pics=[png, story])
before = len(ai_requests)
caption.main()
assert row["status"] == "エラー" and "2枚目" in row["note"], row
assert len(ai_requests) == before

# 作り直しでは、前回のキャプションも渡される
row.update(status="作り直し", pics=[png, png, png], caption="前の文章")
caption.main()
assert "前の文章" in ai_requests[-1]["messages"][0]["content"][-1]["text"]
assert row["status"] == "確認待ち"

# ---------- 3. 投稿 ----------
def run_publish():
    prepare.main()
    publish.main()

# 承認がなければ何もしない
calls.clear()
run_publish()
assert not [c for c in calls if c[0] == "ig"]

# 確認だけのモードでは、何も書き換えず投稿もしない
row["status"] = "承認"
os.environ["DRY_RUN"] = "1"
run_publish()
assert not [c for c in calls if c[0] == "ig"] and row["status"] == "承認"

# 本番: 画像3枚 → カルーセル → 公開。最後に「投稿済み」になる
os.environ["DRY_RUN"] = ""
calls.clear()
run_publish()
posts = [c for c in calls if c[0] == "ig" and c[1] == "POST"]
assert [c[2] for c in posts] == ["123/media"] * 4 + ["123/media_publish"], posts
first = posts[0][3]
assert first["is_carousel_item"] is True
assert first["image_url"].startswith("https://example.com/base/slides/page1/") and first["image_url"].endswith("/01.jpg")
assert posts[3][3]["media_type"] == "CAROUSEL" and posts[3][3]["children"] == "c1,c2,c3"
assert posts[3][3]["caption"] == row["caption"]
assert row["status"] == "投稿済み"
jpgs = sorted((tmp / "slides").rglob("*.jpg"))
assert len(jpgs) >= 3 and Image.open(jpgs[0]).format == "JPEG"

# 画像1枚なら、ふつうの1枚投稿になる
row.update(status="承認", pics=[png])
calls.clear()
run_publish()
posts = [c for c in calls if c[0] == "ig" and c[1] == "POST"]
assert [c[2] for c in posts] == ["123/media", "123/media_publish"]
assert "is_carousel_item" not in posts[0][3] and posts[0][3]["caption"] == row["caption"]

# 「投稿中」で止まった行があれば、二重投稿を避けて止まる
row["status"] = "投稿中"
calls.clear()
try:
    run_publish()
    raise AssertionError("止まるはずが止まらなかった")
except SystemExit:
    pass
assert not [c for c in calls if c[0] == "ig"]

# Instagram 側で失敗したら「エラー」になり、理由が確認メモに入る
row["status"] = "承認"


def failing(method, path, **params):
    raise RuntimeError("Instagram エラー 400: テスト")


instagram._call = failing
try:
    run_publish()
    raise AssertionError("失敗が伝わっていない")
except SystemExit:
    pass
assert row["status"] == "エラー" and "投稿に失敗" in row["note"]

shutil.rmtree(tmp)
print("OK: すべてのテストが通りました")
