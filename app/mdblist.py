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
        list_id can be a numeric ID or a username/listslug format.
        Returns list of dicts with at least: imdb_id, tmdb_id, title, mediatype
        """
        url = f"{MDBLIST_BASE}/lists/{list_id}/items"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"apikey": self.api_key})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "items" in data:
                items = data["items"]
            elif isinstance(data, list):
                items = data
            else:
                logger.warning(f"Unexpected MDBList response format for list {list_id}: {data}")
                return []
            # Filter only movies
            return [i for i in items if i.get("mediatype", "movie") == "movie"]

    async def get_list_info(self, list_id: str) -> dict:
        url = f"{MDBLIST_BASE}/lists/{list_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"apikey": self.api_key})
            r.raise_for_status()
            return r.json()
