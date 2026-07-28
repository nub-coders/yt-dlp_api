import httpx
import re
import json
from urllib.parse import urlparse, parse_qs
from typing import List, Dict


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def extract_playlist_id(value: str) -> str:
    value = value.strip()

    if value.startswith(("PL", "OL", "UU", "RD")):
        return value

    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    pid = query.get("list", [None])[0]

    if not pid:
        raise ValueError("Invalid playlist link or ID")

    return pid


def extract_video_id(value: str) -> str | None:
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    return query.get("v", [None])[0]


async def fetch_page(url: str) -> str:
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def extract_yt_initial_data(html: str) -> dict:
    match = re.search(
        r"ytInitialData\s*=\s*({.*?});\s*</script>",
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Unable to extract ytInitialData")

    return json.loads(match.group(1))


def get_text(obj) -> str:
    if not obj:
        return ""

    if "simpleText" in obj:
        return obj["simpleText"]

    if "runs" in obj:
        return "".join(r.get("text", "") for r in obj["runs"])

    return ""


def make_thumbnail(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"


def parse_normal_playlist(data: dict) -> List[Dict]:
    songs = []

    tabs = (
        data.get("contents", {})
        .get("twoColumnBrowseResultsRenderer", {})
        .get("tabs", [])
    )

    for tab in tabs:
        content = tab.get("tabRenderer", {}).get("content")
        if not content:
            continue

        sections = content.get("sectionListRenderer", {}).get("contents", [])

        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])

            for item in items:
                videos = (
                    item.get("playlistVideoListRenderer", {})
                    .get("contents", [])
                )

                for v in videos:
                    r = v.get("playlistVideoRenderer")
                    if not r:
                        continue

                    vid = r.get("videoId")
                    if not vid:
                        continue

                    songs.append(
                        {
                            "videoId": vid,
                            "title": get_text(r.get("title")),
                            "channel": get_text(r.get("shortBylineText")),
                            "duration": r.get("lengthSeconds", "N/A"),
                            "url": f"https://music.youtube.com/watch?v={vid}",
                            "thumbnail": make_thumbnail(vid),
                        }
                    )

    return songs


def parse_mix_playlist(data: dict) -> List[Dict]:
    songs = []

    panels = (
        data.get("contents", {})
        .get("twoColumnWatchNextResults", {})
        .get("playlist", {})
        .get("playlist", {})
        .get("contents", [])
    )

    for item in panels:
        r = item.get("playlistPanelVideoRenderer")
        if not r:
            continue

        vid = r.get("videoId")
        if not vid:
            continue

        songs.append(
            {
                "videoId": vid,
                "title": get_text(r.get("title")),
                "channel": get_text(r.get("shortBylineText")),
                "duration": r.get("lengthSeconds", "N/A"),
                "url": f"https://music.youtube.com/watch?v={vid}",
                "thumbnail": make_thumbnail(vid),
            }
        )

    return songs


async def extract_playlist(input_value: str) -> List[Dict]:
    playlist_id = extract_playlist_id(input_value)

    if playlist_id.startswith("RD"):
        video_id = (
            extract_video_id(input_value)
            or playlist_id.replace("RD", "", 1)
        )

        url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
        html = await fetch_page(url)
        data = extract_yt_initial_data(html)

        return parse_mix_playlist(data)

    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    html = await fetch_page(url)
    data = extract_yt_initial_data(html)

    return parse_normal_playlist(data)


__all__ = ["extract_playlist"]
