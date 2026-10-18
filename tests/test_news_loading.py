"""Regression coverage for scheduler-free news loading and separate cooldowns."""
from datetime import datetime
from unittest.mock import AsyncMock

from test_transaction_safety import db, client, headers
import main
from models.portable_models import Holding, NewsArticle, User


def add_holding(db):
    db.add(Holding(user_id=1, symbol='ABC.NS', quantity=1, avg_price=100, asset_type='STOCK'))
    db.commit()


def test_empty_news_bootstraps_and_is_then_cached(client, db, monkeypatch):
    main._news_refresh_attempts.clear()
    add_holding(db)
    async def ingest(service, uid):
        db.add(NewsArticle(symbol='ABC.NS', title='ABC earnings', link='https://example.invalid/news'))
        db.commit()
        return 1
    # Use an async implementation directly so the inserted rows are awaited.
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', ingest)
    first = client.get('/news/1', headers=headers())
    assert first.status_code == 200
    assert first.json()[0]['title'] == 'ABC earnings'
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', AsyncMock(side_effect=AssertionError('cached news should not fetch')))
    assert client.get('/news/1', headers=headers()).json() == first.json()


def test_empty_provider_results_are_throttled(client, db, monkeypatch):
    main._news_refresh_attempts.clear()
    add_holding(db)
    ingest = AsyncMock(return_value=0)
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', ingest)
    assert client.get('/news/1', headers=headers()).json() == []
    assert client.get('/news/1', headers=headers()).json() == []
    assert ingest.await_count == 1
    assert client.post('/news/1/ingest', headers=headers()).status_code == 429
    main._news_refresh_attempts[1] -= 61
    assert client.get('/news/1', headers=headers()).status_code == 200
    assert ingest.await_count == 2


def test_news_refresh_independent_of_analytics(client, db, monkeypatch):
    main._news_refresh_attempts.clear()
    user = db.get(User, 1)
    user.last_intelligence_refresh = datetime.now()
    db.add(user); db.commit()
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', AsyncMock(return_value=0))
    assert client.post('/news/1/ingest', headers=headers()).status_code == 200
    assert db.get(User, 1).last_intelligence_refresh == user.last_intelligence_refresh


def test_news_requires_owner_and_holdings(client, db, monkeypatch):
    main._news_refresh_attempts.clear()
    ingest = AsyncMock(return_value=0)
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', ingest)
    assert client.get('/news/1').status_code == 401
    assert client.get('/news/2', headers=headers()).status_code == 403
    assert client.get('/news/1', headers=headers()).json() == []
    ingest.assert_not_awaited()


def test_provider_failure_is_retryable_error(client, db, monkeypatch):
    main._news_refresh_attempts.clear()
    add_holding(db)
    monkeypatch.setattr(main.PortableNewsService, 'ingest_news_for_user', AsyncMock(side_effect=RuntimeError('upstream unavailable')))
    response = client.get('/news/1', headers=headers())
    assert response.status_code == 503
    assert 'temporarily unavailable' in response.json()['detail']
