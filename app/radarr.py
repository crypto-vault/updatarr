import logging
from typing import Optional
import httpx

logger = logging.getLogger("updatarr.radarr")


class RadarrClient:
    def __init__(self, url: str, api_key: str):
        self.base = url.rstrip("/")
        self.headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v3{path}"

    async def get_movies(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self._url("/movie"), headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def get_quality_profiles(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self._url("/qualityprofile"), headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def get_root_folders(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self._url("/rootfolder"), headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def update_movie(self, movie: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(
                self._url(f"/movie/{movie['id']}"),
                headers=self.headers,
                json=movie
            )
            r.raise_for_status()
            return r.json()

    async def add_movie(self, tmdb_id: int, quality_profile_id: int,
                        root_folder: str, monitored: bool,
                        minimum_availability: str, search_on_add: bool) -> dict:
        payload = {
            "tmdbId": tmdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": monitored,
            "minimumAvailability": minimum_availability,
            "addOptions": {
                "searchForMovie": search_on_add,
            }
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(self._url("/movie"), headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def lookup_movie(self, tmdb_id: int) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                self._url("/movie/lookup"),
                headers=self.headers,
                params={"term": f"tmdb:{tmdb_id}"}
            )
            r.raise_for_status()
            results = r.json()
            return results[0] if results else None
