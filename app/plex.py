import logging
import xml.etree.ElementTree as ET
import httpx

from . import database

logger = logging.getLogger("updatarr.plex")

PLEX_TV_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": "updatarr",
}


async def fetch_plex_rss_urls(token: str) -> dict:
    """
    Fetch own and friends watchlist RSS URLs from plex.tv using the user's token.
    Returns dict with 'rss_own' and 'rss_friends' keys.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            "https://plex.tv/api/v2/user",
            params={"X-Plex-Token": token},
            headers=PLEX_TV_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        uuid = data.get("uuid")
        if not uuid:
            raise ValueError("No uuid in plex.tv user response")
        return {
            "rss_own":     f"https://rss.plex.tv/{uuid}",
            "rss_friends": f"https://rss.plex.tv/{uuid}/friends",
        }


class PlexRSSClient:
    def __init__(self, rss_own: str | None = None, rss_friends: str | None = None):
        self.rss_own = rss_own
        self.rss_friends = rss_friends

    async def get_watchlist(self) -> list[dict]:
        """
        Fetch movies from one or both Plex RSS watchlist feeds.

        Uses conditional HTTP requests (If-None-Match / If-Modified-Since) so
        Plex servers return 304 Not Modified when the list hasn't changed,
        saving bandwidth and avoiding unnecessary load on their CDN.

        Returns list of dicts with: title, year, imdb_id (str like 'tt1234567').
        Feeds that return 304 are silently skipped — no items to process.
        """
        movies = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            if self.rss_own:
                logger.info("[Plex] Fetching own watchlist RSS...")
                cache = await database.get_rss_cache(self.rss_own)
                items, new_etag, new_lm, is_304 = await self._fetch_rss(
                    client, self.rss_own, label="own",
                    etag=cache.etag if cache else None,
                    last_modified=cache.last_modified if cache else None,
                )
                if not is_304:
                    await database.set_rss_cache(self.rss_own, new_etag, new_lm)
                    movies.extend(items)

            if self.rss_friends:
                logger.info("[Plex] Fetching friends watchlist RSS...")
                cache = await database.get_rss_cache(self.rss_friends)
                items, new_etag, new_lm, is_304 = await self._fetch_rss(
                    client, self.rss_friends, label="friends",
                    etag=cache.etag if cache else None,
                    last_modified=cache.last_modified if cache else None,
                )
                if not is_304:
                    await database.set_rss_cache(self.rss_friends, new_etag, new_lm)
                    # Deduplicate by imdb_id against what we already have
                    existing_imdb = {m["imdb_id"] for m in movies if m["imdb_id"]}
                    new_items = [i for i in items if i["imdb_id"] not in existing_imdb]
                    logger.info(f"  {len(new_items)} unique items after deduplication")
                    movies.extend(new_items)

        if movies:
            resolved = sum(1 for m in movies if m["imdb_id"])
            logger.info(f"[Plex] Total: {len(movies)} movies, {resolved} with IMDB ID")
            if resolved < len(movies):
                logger.warning(f"  {len(movies) - resolved} items had no IMDB ID and will be skipped")

        return movies

    async def _fetch_rss(
        self,
        client: httpx.AsyncClient,
        url: str,
        label: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> tuple[list[dict], str | None, str | None, bool]:
        """
        Fetch a single RSS feed with conditional request headers.

        Returns (items, new_etag, new_last_modified, is_304).
        When is_304=True the feed is unchanged — items is empty, caller should skip.
        """
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            r = await client.get(url, headers=headers)
        except Exception as e:
            logger.error(f"  Failed to fetch {label} RSS feed: {e}")
            return [], None, None, False

        if r.status_code == 304:
            logger.info(f"  [{label}] 304 Not Modified — no changes since last fetch, skipping")
            return [], None, None, True

        try:
            r.raise_for_status()
        except Exception as e:
            logger.error(f"  Failed to fetch {label} RSS feed: HTTP {r.status_code} — {e}")
            return [], None, None, False

        new_etag = r.headers.get("ETag")
        new_last_modified = r.headers.get("Last-Modified")

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            logger.error(f"  Failed to parse {label} RSS XML: {e}")
            return [], new_etag, new_last_modified, False

        items = []
        channel = root.find("channel")
        if channel is None:
            logger.warning(f"  No <channel> found in {label} RSS feed")
            return [], new_etag, new_last_modified, False

        for item in channel.findall("item"):
            title_el = item.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else "Unknown"

            # Year is sometimes in the title like "Movie Title (2023)" or in a separate tag
            year = None
            if title.endswith(")") and "(" in title:
                try:
                    year = int(title[title.rfind("(") + 1:-1])
                    title = title[:title.rfind("(")].strip()
                except ValueError:
                    pass

            # IMDB ID is in the <guid> tag, format: "imdb://tt1234567"
            guid_el = item.find("guid")
            imdb_id = None
            if guid_el is not None and guid_el.text:
                raw = guid_el.text.strip()
                if raw.startswith("imdb://"):
                    imdb_id = raw.replace("imdb://", "")
                elif raw.startswith("tt"):
                    imdb_id = raw

            items.append({
                "title": title,
                "year": year,
                "imdb_id": imdb_id,
            })

        logger.info(f"  {label} feed: {len(items)} items parsed")
        return items, new_etag, new_last_modified, False
