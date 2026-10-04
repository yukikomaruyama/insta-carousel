"""Instagram API(Instagram ログイン方式)で画像を投稿する。"""
from __future__ import annotations

import os
import time

import requests

from .common import CONFIG, require

HOST = "https://graph.instagram.com"


def _call(method: str, path: str, **params) -> dict:
    url = f"{HOST}/{CONFIG['graph_api_version']}/{path}"
    headers = {"Authorization": f"Bearer {require('IG_ACCESS_TOKEN')}"}
    if method == "GET":
        r = requests.get(url, headers=headers, params=params, timeout=60)
    else:
        r = requests.post(url, headers=headers, json=params, timeout=60)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not r.ok or "error" in data:
        err = data.get("error", {})
        raise RuntimeError(
            f"Instagram エラー {r.status_code}: {err.get('message', r.text[:300])} "
            f"(code {err.get('code')}, subcode {err.get('error_subcode')})"
        )
    return data


def account_id() -> str:
    explicit = os.environ.get("IG_USER_ID", "").strip()
    if explicit:
        return explicit
    me = _call("GET", "me", fields="user_id,username")
    return str(me.get("user_id") or me["id"])


def wait_ready(container_id: str, minutes: int = 5) -> None:
    """コンテナの準備が終わるまで待つ(公式の目安: 1分おきに最大5分)。"""
    for _ in range(minutes * 6):
        status = _call("GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"画像の受け付けに失敗しました(状態: {status})")
        time.sleep(10)
    raise RuntimeError("画像の準備が時間内に終わりませんでした")


def publish(image_urls: list[str], caption: str) -> dict:
    """1枚なら通常の投稿、2〜10枚ならカルーセルとして投稿する。

    {'id': 投稿ID, 'permalink': 投稿URL} を返す。
    """
    if not 1 <= len(image_urls) <= 10:
        raise ValueError(f"画像は1〜10枚です(今は{len(image_urls)}枚)")
    ig = account_id()
    if len(image_urls) == 1:
        container = _call("POST", f"{ig}/media", image_url=image_urls[0], caption=caption)
    else:
        children = []
        for url in image_urls:
            child = _call("POST", f"{ig}/media", image_url=url, is_carousel_item=True)
            children.append(child["id"])
        for child_id in children:
            wait_ready(child_id)
        container = _call("POST", f"{ig}/media", media_type="CAROUSEL",
                          children=",".join(children), caption=caption)
    wait_ready(container["id"])
    published = _call("POST", f"{ig}/media_publish", creation_id=container["id"])
    media_id = published["id"]
    try:
        permalink = _call("GET", media_id, fields="permalink").get("permalink", "")
    except Exception:
        permalink = ""
    return {"id": media_id, "permalink": permalink}
