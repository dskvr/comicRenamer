"""Conservative, rate-limited series year lookup using the Comic Vine API."""
import json
import re
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ComicVine:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.cache = {}
        self.next_request = 0.0
        self.disabled = False

    def lookup_year(self, title: str) -> Optional[str]:
        """Return a year only for a single exact volume name match."""
        key = " ".join(title.casefold().split())
        if key in self.cache:
            return self.cache[key]
        if self.disabled or not self.api_key:
            return None
        self.cache[key] = None
        matches = []
        offset = 0
        print(f"COMICVINE : Looking up {title}")
        try:
            # Bound unexpectedly broad searches; incomplete results never win.
            for _ in range(5):
                delay = self.next_request - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.next_request = time.monotonic() + 18.1
                query = urlencode({
                    "api_key": self.api_key, "format": "json",
                    "filter": "name:" + title, "field_list": "id,name,start_year",
                    "limit": 100, "offset": offset,
                })
                request = Request("https://comicvine.gamespot.com/api/volumes/?" + query,
                                  headers={"User-Agent": "comicRenamer/1.0 (https://github.com/dskvr/comicRenamer)",
                                           "Accept": "application/json"})
                with urlopen(request, timeout=20) as response:
                    data = json.load(response)
                if not isinstance(data, dict) or data.get("status_code") != 1:
                    raise ValueError("API rejected request")
                results = data.get("results")
                total = data.get("number_of_total_results")
                if not isinstance(results, list) or type(total) is not int or total < 0:
                    raise ValueError("Invalid response")
                for result in results:
                    if not isinstance(result, dict) or not isinstance(result.get("name"), str):
                        raise ValueError("Invalid volume")
                    if " ".join(result["name"].casefold().split()) == key:
                        matches.append(result)
                offset += len(results)
                if offset >= total:
                    if len(matches) == 1:
                        year = str(matches[0].get("start_year", ""))
                        if re.fullmatch(r"[12]\d{3}", year):
                            self.cache[key] = year
                    if not self.cache[key]:
                        print(f"COMICVINE : No unambiguous series year for {title}")
                    return self.cache[key]
                if not results:
                    raise ValueError("Incomplete response")
            print(f"COMICVINE : Search incomplete for {title}; leaving year unresolved")
        except (HTTPError, URLError, OSError, ValueError, TypeError):
            # Never print exception text: request URLs contain the API key.
            self.disabled = True
            print("COMICVINE : Lookup failed; disabling further requests for this run. "
                  "Check the API key, connection, and rate limit.")
        return None
