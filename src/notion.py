"""Notion の投稿リスト(データベース)の読み書き。"""
from __future__ import annotations

import time

import requests

from .common import CONFIG, require

API = "https://api.notion.com/v1"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {require('NOTION_TOKEN')}",
        "Notion-Version": CONFIG["notion_version"],
        "Content-Type": "application/json",
    }


def _call(method: str, path: str, body: dict | None = None) -> dict:
    for attempt in range(4):
        r = requests.request(method, API + path, headers=_headers(), json=body, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        break
    if not r.ok:
        raise RuntimeError(f"Notion エラー {r.status_code}: {r.text[:400]}")
    return r.json()


def query(status: str, limit: int = 10) -> list[dict]:
    """指定ステータスのページを、作られた順(古い順)に返す。"""
    body = {
        "filter": {"property": "ステータス", "select": {"equals": status}},
        "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        "page_size": limit,
    }
    path = f"/data_sources/{CONFIG['notion_data_source_id']}/query"
    return _call("POST", path, body)["results"]


def text_of(page: dict, prop: str) -> str:
    p = page["properties"].get(prop, {})
    parts = p.get("title") or p.get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in parts).strip()


def rich(text: str) -> list[dict]:
    """Notion は1かたまり2000文字までなので分割する。"""
    return [{"type": "text", "text": {"content": text[i : i + 1900]}} for i in range(0, len(text), 1900)]


def update(page_id: str, *, status: str | None = None, caption: str | None = None,
           note: str | None = None, posted_on: str | None = None, url: str | None = None) -> None:
    props: dict = {}
    if status is not None:
        props["ステータス"] = {"select": {"name": status}}
    if caption is not None:
        props["キャプション"] = {"rich_text": rich(caption)}
    if note is not None:
        props["確認メモ"] = {"rich_text": rich(note)}
    if posted_on is not None:
        props["投稿日"] = {"date": {"start": posted_on}}
    if url is not None:
        props["投稿URL"] = {"url": url}
    _call("PATCH", f"/pages/{page_id}", {"properties": props})


def children(page_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        q = f"/blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = _call("GET", q)
        out += data["results"]
        if not data.get("has_more"):
            return out
        cursor = data["next_cursor"]


def image_urls(page_id: str) -> list[str]:
    """ページ本文に並んでいる画像のURLを、上から順に返す。

    Notion にアップロードした画像のURLは約1時間で切れるので、使う直前に取り直すこと。
    """
    urls = []
    for block in children(page_id):
        if block["type"] != "image":
            continue
        img = block["image"]
        urls.append(img[img["type"]]["url"])
    return urls


def report_error(page_id: str, message: str) -> None:
    """ステータスを「エラー」にして、理由を確認メモに書く。"""
    try:
        update(page_id, status="エラー", note=f"⚠️ {message[:1800]}")
    except Exception as e:  # エラー報告の失敗で元のエラーを隠さない
        print(f"Notion へのエラー記録に失敗: {e}")
