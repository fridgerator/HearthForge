"""
SearXNG search client for the tool agent.

Queries a self-hosted SearXNG instance to find current API documentation,
working endpoints, and code examples before the tool agent writes its script.

This is what makes the tool agent "search-native" like Perplexity Computer:
instead of guessing at API endpoints from training data, the agent searches
for current information first.

SearXNG setup: see docker-compose.searxng.yml in this project.
Default endpoint: http://localhost:8080
"""

import logging

import httpx

from config import SEARXNG_URL

logger = logging.getLogger(__name__)


class SearchClient:
    def __init__(self, base_url: str = SEARXNG_URL):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    async def search(
        self,
        query: str,
        max_results: int = 5,
        categories: str = "general",
    ) -> list[dict]:
        """
        Search SearXNG and return parsed results.

        Returns list of:
          {"title": "...", "url": "...", "snippet": "..."}
        """
        params = {
            "q": query,
            "format": "json",
            "categories": categories,
            "language": "en",
            "safesearch": 0,
        }

        try:
            url = f"{self.base_url}/search"
            logger.debug(f"SearXNG search: {query}")
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("results", [])[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                })

            logger.debug(f"SearXNG returned {len(results)} results for: {query}")
            return results

        except httpx.ConnectError:
            logger.warning(f"Cannot connect to SearXNG at {self.base_url}. Is it running?")
            return []
        except Exception as e:
            logger.warning(f"SearXNG search failed: {e}")
            return []

    async def search_for_api_docs(
        self,
        task_description: str,
        llm_client=None,
    ) -> str:
        """
        High-level method: given a task description, formulate a good
        search query, run it, and return formatted results suitable
        for injection into the tool agent's prompt.

        If llm_client is provided, uses the LLM to formulate the search
        query. Otherwise, extracts keywords heuristically.
        """
        # Step 1: Formulate search query
        if llm_client:
            query = await self._llm_formulate_query(task_description, llm_client)
        else:
            query = self._heuristic_query(task_description)

        if not query:
            return ""

        # Step 2: Run search
        results = await self.search(query, max_results=5)

        if not results:
            # Try a broader fallback query
            fallback = self._heuristic_query(task_description)
            if fallback != query:
                results = await self.search(fallback, max_results=5)

        if not results:
            return ""

        # Step 3: Format for prompt injection
        return self._format_results(query, results)

    async def _llm_formulate_query(self, task_description: str, llm_client) -> str:
        """Use the LLM to create an optimal search query from a task description."""
        try:
            response = await llm_client.chat(
                system_prompt=(
                    "You are a search query generator. Given a task description about "
                    "fetching data from an API, generate a short, specific web search query "
                    "that would find current API documentation or working code examples.\n\n"
                    "Rules:\n"
                    "- Output ONLY the search query, nothing else\n"
                    "- Keep it under 10 words\n"
                    "- Include the API/service name if mentioned\n"
                    "- Include 'API' and relevant terms like 'endpoint', 'free', 'no auth'\n"
                    "- Include the current year for freshness\n"
                    "- Example: 'coingecko free API bitcoin price history endpoint 2026'"
                ),
                user_message=task_description,
                temperature=0.1,
            )
            query = response.strip().strip('"').strip("'")
            # Sanity check — if the LLM returned something too long, it's not a query
            if len(query) > 150 or "\n" in query:
                return self._heuristic_query(task_description)
            logger.debug(f"LLM-generated search query: {query}")
            return query
        except Exception as e:
            logger.warning(f"LLM query generation failed: {e}")
            return self._heuristic_query(task_description)

    def _heuristic_query(self, task_description: str) -> str:
        """
        Simple keyword extraction for search query formulation.
        Fallback when the LLM isn't available.
        """
        # Known data sources to look for
        known_sources = [
            "coingecko", "yahoo finance", "alpha vantage", "open-meteo",
            "binance", "coinbase", "finnhub", "polygon", "iex cloud",
            "fred", "world bank", "openweathermap", "newsapi",
        ]

        desc_lower = task_description.lower()
        source = None
        for s in known_sources:
            if s in desc_lower:
                source = s
                break

        # Extract key data terms
        data_terms = []
        data_keywords = [
            "price", "history", "historical", "chart", "ohlc", "ohlcv",
            "fundamentals", "market cap", "volume", "weather", "forecast",
            "temperature", "earnings", "revenue", "quote", "ticker",
            "exchange rate", "forex", "crypto", "bitcoin", "ethereum",
        ]
        for kw in data_keywords:
            if kw in desc_lower:
                data_terms.append(kw)

        parts = []
        if source:
            parts.append(source)
        parts.append("free API")
        if data_terms:
            parts.extend(data_terms[:3])  # Don't over-stuff the query
        parts.append("endpoint 2026")

        return " ".join(parts)

    def _format_results(self, query: str, results: list[dict]) -> str:
        """Format search results for injection into the tool agent's prompt."""
        lines = [
            f"## Web Search Results for: \"{query}\"",
            "Use this information to write a script with the correct, current API endpoints:\n",
        ]

        for i, r in enumerate(results, 1):
            lines.append(f"### Result {i}: {r['title']}")
            lines.append(f"URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"Snippet: {r['snippet']}")
            lines.append("")

        return "\n".join(lines)

    async def is_available(self) -> bool:
        """Check if SearXNG is reachable."""
        try:
            response = await self._client.get(f"{self.base_url}/healthz")
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()
