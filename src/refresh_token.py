"""Instagram のアクセストークン(60日で失効)を延長し、GitHub Secrets に保存し直す。

GitHub Actions から月2回動かす。GH_TOKEN には Secrets を書き換えられる
個人アクセストークン(GH_PAT)を渡す。

    python -m src.refresh_token
"""
from __future__ import annotations

import os
import subprocess

import requests

from .common import require


def main() -> None:
    r = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": require("IG_ACCESS_TOKEN")},
        timeout=30,
    )
    data = r.json()
    if not r.ok or "access_token" not in data:
        raise SystemExit(f"トークンの延長に失敗しました: {r.text[:300]}")
    token = data["access_token"]
    print(f"::add-mask::{token}")  # ログに出ないよう隠す
    days = int(data.get("expires_in", 0)) // 86400
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN"], input=token, text=True, check=True)
        print(f"トークンを延長して保存しました(あと約{days}日有効)")
    else:
        print(f"トークンを延長しました(あと約{days}日有効)。新しい値を .env の IG_ACCESS_TOKEN に入れてください。")
        print(token)


if __name__ == "__main__":
    main()
