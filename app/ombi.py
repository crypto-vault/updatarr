import logging
import httpx

logger = logging.getLogger("updatarr.ombi")


class OmbiClient:
    def __init__(self, url: str, api_key: str):
        self.base = url.rstrip("/")
        self.headers = {
            "ApiKey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_movie_requests(self, approved_only: bool = True) -> list[dict]:
        """
        Fetch movie requests from Ombi.
        Returns list of dicts with: title, tmdb_id, denied, approved, available

        Each item shape from /api/v1/Request/movie:
        {
          "theMovieDbId": 12345,
          "title": "Movie Title",
          "denied": false,
          "approved": true,
          "available": false,
          ...
        }
        """
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{self.base}/api/v1/Request/movie",
                headers=self.headers,
            )
            r.raise_for_status()
            requests = r.json()

        if not isinstance(requests, list):
            logger.warning(f"Unexpected Ombi response format: {type(requests)}")
            return []

        results = []
        for req in requests:
            # Skip denied requests always
            if req.get("denied"):
                continue
            # Skip if approved_only and not yet approved
            if approved_only and not req.get("approved"):
                continue

            tmdb_id = req.get("theMovieDbId")
            if not tmdb_id:
                logger.debug(f"Ombi request '{req.get('title')}' has no TMDB ID, skipping")
                continue

            results.append({
                "title":     req.get("title", f"TMDB:{tmdb_id}"),
                "tmdb_id":   int(tmdb_id),
                "approved":  req.get("approved", False),
                "available": req.get("available", False),
            })

        status = "approved" if approved_only else "non-denied"
        logger.info(f"Ombi: {len(results)} {status} movie requests (of {len(requests)} total)")
        return results

    async def validate(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.base}/api/v1/Settings/ombi",
                    headers=self.headers,
                )
                if r.status_code == 200:
                    return True, "OK"
                return False, f"HTTP {r.status_code} — check URL and API key"
        except Exception as e:
            return False, str(e)
