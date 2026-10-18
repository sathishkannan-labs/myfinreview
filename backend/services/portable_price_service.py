import asyncio
import logging
import httpx
from typing import List
from sqlmodel import Session, select
from models.portable_models import PriceCache
from providers.yahoo_finance_provider import YahooFinanceProvider
from providers.mfapi_provider import MFAPIProvider
from datetime import datetime, timedelta
from config import settings

from services.reference_data_service import ReferenceDataService

logger = logging.getLogger(__name__)

class PortablePriceService:
    def __init__(self, session: Session):
        self.session = session
        self.ref_data = ReferenceDataService() # Singleton instance
        self.stock_provider = YahooFinanceProvider(
            timeout=settings.PROVIDER_TIMEOUT,
            retries=settings.PROVIDER_RETRIES
        )
        self.mf_provider = MFAPIProvider(
            timeout=settings.PROVIDER_TIMEOUT,
            retries=settings.PROVIDER_RETRIES
        )
        self.cache_duration_minutes = settings.PRICE_CACHE_MINUTES

    async def get_prices_for_symbols(self, symbols: List[str], refresh_stale: bool = False) -> dict:
        """
        Optimized bulk fetch for a list of symbols (async).
        Default (refresh_stale=False): Returns cache if available (even if stale), only fetches missing.
        """
        if not symbols:
            return {}
        
        # 1. Bulk DB Lookup
        statement = select(PriceCache).where(PriceCache.symbol.in_(symbols))
        cached_items = self.session.exec(statement).all()
        cache_map = {c.symbol: c for c in cached_items}
        
        results = {}
        now = datetime.now()
        
        # helper returning TTL based on asset type (stocks vs MF)
        def ttl_for(sym: str) -> int:
            if str(sym).isdigit():
                return settings.MF_PRICE_CACHE_MINUTES
            return settings.STOCK_PRICE_CACHE_MINUTES
        
        # determine which symbols need a provider call
        to_fetch = []
        for symbol in symbols:
            cached = cache_map.get(symbol)
            
            if not cached or cached.price <= 0:
                # Always fetch if missing
                to_fetch.append(symbol)
            elif refresh_stale:
                # Fetch if stale only if specifically requested
                expiry = ttl_for(symbol)
                if now - cached.timestamp >= timedelta(minutes=expiry):
                    to_fetch.append(symbol)
                else:
                    results[symbol] = cached.price
            else:
                # fast-path: return cached price even if stale
                results[symbol] = cached.price
        
        if to_fetch:
            # Group by provider type and use batch fetch
            stocks = [s for s in to_fetch if not str(s).isdigit()]
            mfs = [s for s in to_fetch if str(s).isdigit()]
            
            tasks = []
            if stocks: tasks.append(self.stock_provider.get_batch_prices(stocks))
            if mfs: tasks.append(self.mf_provider.get_batch_prices(mfs))
            
            if tasks:
                batch_results = await asyncio.gather(*tasks)
                for res in batch_results:
                    if not res: continue
                    for sym, data in res.items():
                        # Providers now return {"price": x, "name": y} OR just price
                        price = data.get("price") if isinstance(data, dict) else data
                        name = data.get("name") if isinstance(data, dict) else None
                        
                        results[sym] = price
                        # Update cache with price and name (if provided)
                        if not name:
                            # Fallback to local reference data for stocks
                            name = self.ref_data.get_asset_name(sym)
                        
                        self.save_price(sym, price, name=name, commit=False)
                
                # Commit all updates at once
                self.session.commit()

        return results

    def _get_provider_for_symbol(self, symbol: str):
        if str(symbol).isdigit():
            return self.mf_provider
        return self.stock_provider

    def get_latest_price_data(self, symbol: str):
        """Returns the full PriceCache object for a symbol"""
        statement = select(PriceCache).where(PriceCache.symbol == symbol)
        return self.session.exec(statement).first()

    async def get_latest_price(self, symbol: str, refresh_stale: bool = False):
        cached = self.get_latest_price_data(symbol)
        
        if cached and cached.price > 0:
            if not refresh_stale:
                return cached.price # Return stale cache immediately
            
            expiry = settings.MF_PRICE_CACHE_MINUTES if str(symbol).isdigit() else settings.STOCK_PRICE_CACHE_MINUTES
            if datetime.now() - cached.timestamp < timedelta(minutes=expiry):
                return cached.price

        if str(symbol).isdigit():
            if symbol in self.ref_data.mf_cache:
                price = self.ref_data.mf_cache[symbol].get('nav')
                if price:
                    self.save_price(symbol, price, name=self.ref_data.mf_cache[symbol].get('name'))
                    return price

        provider = self._get_provider_for_symbol(symbol)
        # provider methods may be async
        if asyncio.iscoroutinefunction(provider.get_price):
            price_data = await provider.get_price(symbol)
        else:
            price_data = provider.get_price(symbol)
        
        if price_data:
            # Handle cases where provider might return a dict instead of float
            price = price_data.get("price") if isinstance(price_data, dict) else price_data
            
            asset_name = self.ref_data.get_asset_name(symbol)
            self.save_price(symbol, price, name=asset_name)
            return price
        
        return cached.price if cached else None

    def save_price(self, symbol: str, price: float, name: str = None, commit: bool = True):
        statement = select(PriceCache).where(PriceCache.symbol == symbol)
        cached_price = self.session.exec(statement).first()
        
        # Validation: Never save symbol as name
        if name and name.strip().upper() == symbol.strip().upper():
            name = None

        if cached_price:
            cached_price.price = float(price)
            cached_price.timestamp = datetime.now()
            # Only update name if a valid new one is provided
            if name: cached_price.name = name
        else:
            cached_price = PriceCache(symbol=symbol, price=float(price), name=name)
            self.session.add(cached_price)
        
        if commit:
            self.session.commit()
            self.session.refresh(cached_price)

    def update_asset_name(self, symbol: str, name: str):
        """Updates the name in PriceCache without touching the price or price timestamp."""
        if not name or name.strip().upper() == symbol.strip().upper():
            return

        statement = select(PriceCache).where(PriceCache.symbol == symbol)
        cached = self.session.exec(statement).first()
        
        if cached:
            cached.name = name
        else:
            # Create entry with 0 price if it doesn't exist yet
            cached = PriceCache(symbol=symbol, price=0.0, name=name, timestamp=datetime.now() - timedelta(days=1))
            self.session.add(cached)
        
        self.session.commit()

    async def get_price_with_metadata(self, symbol: str) -> dict:
        """
        Specialized fetch for indices that returns price and metadata (like prev_close).
        """
        provider = self._get_provider_for_symbol(symbol)
        if hasattr(provider, '_fetch_single_price'):
            async with httpx.AsyncClient(timeout=provider.timeout) as client:
                data = await provider._fetch_single_price(symbol, client)
                if data:
                    # Save to cache as well
                    self.save_price(symbol, data['price'], name=data.get('name'))
                    return data
        
        # Fallback to standard price if provider doesn't support rich data
        price = await self.get_latest_price(symbol)
        return {"price": price, "prev_close": None}

    async def validate_symbol(self, symbol: str) -> bool:
        price = await self.get_latest_price(symbol)
        return price is not None and price > 0
