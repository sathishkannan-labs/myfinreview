import feedparser
import requests
import hashlib
import asyncio
from datetime import datetime
from sqlmodel import Session, select
from models.portable_models import NewsArticle, NewsHash, Holding, AlertRule
from repositories.portable_repository import PortableNewsRepository
from services.portable_price_service import PortablePriceService
from config import settings
import httpx

class PortableNewsService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = PortableNewsRepository(session)
        self.price_service = PortablePriceService(session)
        self.rss_feeds = settings.RSS_FEEDS
        self.api_key = settings.NEWS_API_KEY
        self.api_base_url = settings.NEWS_API_BASE_URL

    async def ingest_news_for_user(self, user_id: int):
        """
        Ingests news for all symbols in a user's portfolio and alert rules.
        """
        # 1. Symbols from Holdings
        statement = select(Holding).where(Holding.user_id == user_id)
        holdings = self.session.exec(statement).all()
        symbols = [h.symbol for h in holdings]
        
        # 2. Symbols from Alert Rules (to include watchlist)
        rules_stmt = select(AlertRule).where(AlertRule.user_id == user_id)
        rules = self.session.exec(rules_stmt).all()
        for r in rules:
            if r.symbol and r.symbol not in symbols:
                symbols.append(r.symbol)

        if not symbols:
            return 0
        
        return await self.fetch_and_store_news(symbols)

    async def fetch_and_store_news(self, symbols: list):
        print(f"DEBUG: Fetching news for symbols: {symbols}")
        
        # 1. Parallel Fetch from all sources
        rss_articles = await self._fetch_all_rss_async(symbols)
        print(f"DEBUG: RSS found {len(rss_articles)} candidate articles")
        
        # 2. NewsData API Backup (if needed)
        api_articles = []
        if len(rss_articles) < 5:
            api_articles = await self._fetch_from_news_api_backup_async(symbols)
            print(f"DEBUG: News API found {len(api_articles)} candidate articles")

        all_news = rss_articles + api_articles
        if not all_news:
            return 0
        
        # 3. Bulk Check for new stories
        existing_hashes = set(h.hash for h in self.session.exec(select(NewsHash)).all())
        
        stored_count = 0
        new_hashes_to_add = []

        for item in all_news:
            title_hash = hashlib.md5(item['title'].encode()).hexdigest()
            if title_hash not in existing_hashes:
                # Identify Category
                content_upper = (item['title'] + " " + item.get('summary', '')).upper()
                category = "NEWS"
                if any(k in content_upper for k in ["ANNOUNCEMENT", "DISCLOSURE", "INTIMATION", "REGULATION 30", "CIRCULAR", "NOTICE"]):
                    category = "ANNOUNCEMENT"
                elif any(k in content_upper for k in ["RESULT", "FINANCIAL PERFORMANCE", "QUARTERLY", "EARNINGS", "PROFIT", "LOSS", "AUDITED"]):
                    category = "RESULT"
                elif any(k in content_upper for k in ["DIVIDEND", "BONUS", "SPLIT", "BUYBACK", "RIGHTS ISSUE", "MERGER", "ACQUISITION", "CORPORATE ACTION"]):
                    category = "ACTION"

                for symbol in item['matched_symbols']:
                    self.repo.save_news_item(
                        symbol,
                        item['title'],
                        item['link'],
                        summary=item.get('summary'),
                        published_at=item.get('published_at'),
                        commit=False,
                        category=category
                    )
                    stored_count += 1
                
                existing_hashes.add(title_hash)
                new_hashes_to_add.append(NewsHash(hash=title_hash))

        # 4. Atomic Bulk Save
        if new_hashes_to_add:
            for nh in new_hashes_to_add:
                self.session.add(nh)
            self.session.commit()
            
        print(f"DEBUG: Stored {stored_count} news items")
        return stored_count

    async def _fetch_all_rss_async(self, symbols: list):
        # Build search map once
        search_map = {str(s).split('.')[0].upper(): s for s in symbols}
        
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            tasks = [self._fetch_single_rss_async(url, search_map, client) for url in self.rss_feeds]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Flatten results
            all_articles = []
            for res in results:
                if isinstance(res, list):
                    all_articles.extend(res)
            return all_articles

    async def _fetch_single_rss_async(self, url: str, search_map: dict, client: httpx.AsyncClient):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            matched = []
            for entry in feed.entries:
                content = (entry.title + " " + entry.get('summary', '')).upper()
                matches = [original for term, original in search_map.items() if term in content]
                if matches:
                    matched.append({
                        'title': entry.title,
                        'link': entry.link,
                        'summary': entry.get('summary', entry.get('description', '')),
                        'published_at': entry.get('published', entry.get('updated', None)),
                        'matched_symbols': list(set(matches))
                    })
            return matched
        except Exception as e:
            print(f"RSS Error for {url}: {e}")
            return []

    async def _fetch_from_news_api_backup_async(self, symbols: list):
        if not self.api_key or self.api_key == 'YOUR_NEWSDATA_IO_KEY':
            return []
        
        search_map = {str(s).split('.')[0].upper(): s for s in symbols}
        query_terms = [str(s).split('.')[0] for s in symbols[:3]]
        query = " OR ".join(query_terms)

        try:
            params = {'apikey': self.api_key, 'q': query, 'country': 'in', 'language': 'en', 'category': 'business'}
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(self.api_base_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    for r in data.get('results', []):
                        content = (r.get('title', '') + " " + r.get('description', '')).upper()
                        matches = [original for term, original in search_map.items() if term in content]
                        if matches:
                            results.append({
                                'title': r['title'],
                                'link': r['link'],
                                'summary': r.get('description',''),
                                'published_at': r.get('pubDate', r.get('publishedAt', None)),
                                'matched_symbols': list(set(matches))
                            })
                    return results
        except Exception as e:
            print(f"News API Error: {e}")
        return []

    def _fetch_from_rss(self, symbols: list):
        # Deprecated: use async version
        return []

    def _fetch_from_news_api_backup(self, symbols: list):
        # Deprecated: use async version
        return []

    def _is_new_story(self, title: str):
        title_hash = hashlib.md5(title.encode()).hexdigest()
        statement = select(NewsHash).where(NewsHash.hash == title_hash)
        return self.session.exec(statement).first() is None

    def _mark_story_as_seen(self, title: str):
        title_hash = hashlib.md5(title.encode()).hexdigest()
        new_hash = NewsHash(hash=title_hash)
        self.session.add(new_hash)
        self.session.commit()
