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

    async def lookup_by_imdb(self, imdb_id: str) -> dict | None:
        """Lookup a movie by IMDB ID using Radarr's own metadata DB."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                self._url("/movie/lookup"),
                headers=self.headers,
                params={"term": f"imdb:{imdb_id}"}
            )
            r.raise_for_status()
            results = r.json()
            return results[0] if results else None

    async def search_movie(self, movie_id: int) -> None:
        """Trigger a movie search via Radarr's command API."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self._url("/command"),
                headers=self.headers,
                json={"name": "MoviesSearch", "movieIds": [movie_id]}
            )
            r.raise_for_status()

    async def delete_movie_file(self, file_id: int) -> None:
        """Delete a movie file (moves to recycle bin if configured in Radarr)."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                self._url(f"/moviefile/{file_id}"),
                headers=self.headers,
            )
            r.raise_for_status()

    async def delete_movie(self, movie_id: int, delete_files: bool = False) -> None:
        """Remove a movie from Radarr. Optionally delete the files from disk."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                self._url(f"/movie/{movie_id}"),
                headers=self.headers,
                params={"deleteFiles": str(delete_files).lower()},
            )
            r.raise_for_status()

    async def get_media_management(self) -> dict:
        """Get Radarr media management settings (includes recycleBin path)."""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(self._url("/config/mediamanagement"), headers=self.headers)
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
