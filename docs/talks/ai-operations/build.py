"""Assemble the offline HTML keynote using only Python's standard library."""
from base64 import b64encode
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def build() -> None:
    assets = {
        "__SHOP_SCREENSHOT__": HERE / "assets/shop-review-workflow.png",
        "__SHOP_ROSTER__": HERE / "assets/shop-team-roster.png",
        "__SHOP_ACCEPTANCE__": HERE / "assets/shop-owner-acceptance.png",
        "__GAME_SCREENSHOT__": HERE / "assets/undercover-game.png",
        "__AVERNET_ICON__": ROOT / "src/frontend/public/Avernet-logo.png",
        "__AVERNET_WORDMARK__": ROOT / "src/frontend/public/Avernet-logotitle.png",
    }
    content = (HERE / "deck-content.js").read_text()
    html = (HERE / "shell.html").read_text()
    html = html.replace("__STYLES__", (HERE / "theme.css").read_text())
    html = html.replace("__CONTENT__", content)
    html = html.replace("__RUNTIME__", (HERE / "runtime.js").read_text())
    for placeholder, asset in assets.items():
        uri = "data:image/png;base64," + b64encode(asset.read_bytes()).decode("ascii")
        html = html.replace(placeholder, uri)
    target = HERE / "index.html"
    target.write_text(html, encoding="utf-8")
    print(f"Built {target} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
