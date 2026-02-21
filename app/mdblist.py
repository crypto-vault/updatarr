import logging
import httpx

logger = logging.getLogger("updatarr.mdblist")

MDBLIST_BASE = "https://mdblist.com/api"


class MDBListClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_list_items(self, list_id: str) -> list[dict]:
        """
        Fetch all movies from an MDBList list.

        Real API response is a dict with an "items" key containing a list of objects.
        Each item has flat fields: id (=tmdb_id), imdbid, title, year, type, mediatype

        mediatype field: "movie" | "show"
        type field may also be "movie" | "show"
        """
        url = f"{MDBLIST_BASE}/lists/{list_id}/items"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"apikey": self.api_key})
            r.raise_for_status()
            data = r.json()

        logger.debug(f"MDBList raw response type: {type(data)}, preview: {str(data)[:300]}")

        # Response is always {"items": [...], "total": N, "response": true}
        if isinstance(data, dict):
            if not data.get("response", True) is False and "error" in data:
                logger.error(f"MDBList API error for list {list_id}: {data.get('error')}")
                return []
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            logger.warning(f"Unexpected MDBList response format for list {list_id}: {data}")
            return []

        if not items:
            logger.warning(f"MDBList returned 0 items for list {list_id}. Check the list ID and API key.")
            return []

        logger.debug(f"Sample item fields: {list(items[0].keys()) if items else 'N/A'}")

        # Filter movies only — field is "mediatype" or "type", value "movie"
        movies = [
            i for i in items
            if i.get("mediatype", i.get("type", "movie")) == "movie"
        ]

        logger.info(f"  {len(movies)} movies (of {len(items)} total items) in list {list_id}")
        return movies

    def extract_tmdb_id(self, item: dict) -> int | None:
        """
        Extract TMDB ID from an item.
        Real API returns TMDB ID in the "id" field on list items.
        """
        return (
            item.get("id")        # real API field — this IS the tmdb id
            or item.get("tmdbid")
            or item.get("tmdb_id")
            or item.get("tmdb")
        ) or None

    def extract_title(self, item: dict) -> str:
        return item.get("title") or item.get("name") or f"TMDB:{self.extract_tmdb_id(item)}"

    async def get_list_info(self, list_id: str) -> dict:
        url = f"{MDBLIST_BASE}/lists/{list_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"apikey": self.api_key})
            r.raise_for_status()
            return r.json()
