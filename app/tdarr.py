import logging

import httpx

logger = logging.getLogger("updatarr.tdarr")


class TdarrClient:
    def __init__(self, url: str, library_id: str):
        self.base = url.rstrip("/")
        self.library_id = library_id

    async def send_file(self, file_path: str) -> None:
        """Submit a specific file to a Tdarr library via scanFolderWatcher.
        Tdarr will scan it, run it through the plugin stack, and queue it for
        transcode if the plugins decide it needs re-encoding."""
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            r = await client.post(
                f"{self.base}/api/v2/scan-files",
                json={
                    "data": {
                        "scanConfig": {
                            "dbID": self.library_id,
                            "mode": "scanFolderWatcher",
                            "arrayOrPath": [file_path],
                        }
                    }
                },
            )
            r.raise_for_status()
            logger.info(f"[Tdarr] Queued '{file_path}' in library '{self.library_id}'")

    async def validate(self) -> tuple[bool, str]:
        """Check Tdarr server reachability and verify the library ID exists."""
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
                r = await client.get(f"{self.base}/api/v2/status")
                if r.status_code != 200:
                    return False, f"Server unreachable (HTTP {r.status_code})"

                # Verify library ID exists
                r2 = await client.post(
                    f"{self.base}/api/v2/cruddb",
                    json={"data": {"collection": "LibrarySettingsJSONDB", "mode": "getAll"}},
                )
                libraries = r2.json() if r2.status_code == 200 else []
                if isinstance(libraries, dict):
                    libraries = libraries.get("data", [])
                match = next((lib for lib in libraries if lib.get("_id") == self.library_id), None)
                if match:
                    return True, f"Connected — library '{match.get('name', self.library_id)}' found"
                # Build helpful list of valid IDs
                valid = [(lib.get("_id"), lib.get("name")) for lib in libraries if lib.get("_id")]
                hint = ", ".join(f"{lid} ({name})" for lid, name in valid[:5])
                return False, f"Library ID '{self.library_id}' not found. Valid IDs: {hint}"
        except Exception as e:
            return False, str(e)
