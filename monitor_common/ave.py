"""AVE Cloud data API collector (https://cloud.ave.ai).

Endpoints and the ``X-API-KEY`` header follow AVE's own client
(https://github.com/AveCloud/ave-cloud-skill, scripts/ave/). The free plan
includes the data REST API; set ``AVE_API_KEY`` before use.

AVE's response fields are not mapped into filter rules yet: reports are
archived raw in ``external_reports`` so they can be compared with this tool's
own decisions before any field is trusted.
"""

from __future__ import annotations

import os
import urllib.parse

from .http import HttpJsonClient

BASE_URL = os.environ.get("AVE_DATA_URL", "https://data.ave-api.xyz/v2")


class AveClient:
    def __init__(
        self,
        api_key: str | None = None,
        http: HttpJsonClient | None = None,
        base_url: str = BASE_URL,
        chain: str = "solana",  # AVE chain name, e.g. solana, eth, bsc
    ):
        self.api_key = api_key or os.environ.get("AVE_API_KEY")
        if not self.api_key:
            raise ValueError("AVE_API_KEY is not set (get one at https://cloud.ave.ai)")
        self.http = http or HttpJsonClient(min_interval_s=1.0)
        self.base_url = base_url.rstrip("/")
        self.chain = chain

    def _get(self, path: str, params: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self.http.request(url, headers={"X-API-KEY": self.api_key})

    def token(self, address: str):
        return self._get(f"/tokens/{address}-{self.chain}")

    def holders(self, address: str, limit: int = 100):
        return self._get(f"/tokens/holders/{address}-{self.chain}", {"limit": limit})

    def contract_risk(self, address: str):
        """AVE's contract risk / honeypot report for a token."""
        return self._get(f"/contracts/{address}-{self.chain}")

    def trending(self, page: int = 0, page_size: int = 50):
        params = {"chain": self.chain, "current_page": page, "page_size": page_size}
        return self._get("/tokens/trending", params)
