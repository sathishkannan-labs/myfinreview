"""Regression checks use local SQLite and mocked providers; never contact production."""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import main
from database import get_session
from models.portable_models import User, Holding, Transaction
from services.portable_portfolio_service import PortablePortfolioService
from services.overlap_service import OverlapService


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv('AUTH_SECRET_KEY', 'regression-test-secret-with-at-least-32-bytes')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(email='owner@test.local', password='HASH_NOT_FOR_RESPONSE'),
                         User(email='other@test.local', password='OTHER_HASH')])
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    main.app.dependency_overrides[get_session] = lambda: db
    monkeypatch.setattr(main, '_refresh_after_transactions', AsyncMock(return_value=True))
    monkeypatch.setattr(main.PortablePriceService, 'validate_symbol', AsyncMock(return_value=True))
    # No lifespan: use dependency-provided ephemeral database, not configured DB.
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def headers():
    return {'Authorization': 'Bearer ' + main._issue_token(1)}


def row(**kwargs):
    return dict(user_id=1, symbol='ABC.NS', type='BUY', quantity=10, price=100, **kwargs)


def test_mixed_user_batch_rejected_before_writes(client, db):
    other = row(); other['user_id'] = 2
    response = client.post('/portfolio/bulk-transaction', json=[row(), other], headers=headers())
    assert response.status_code == 403
    assert db.exec(select(Transaction)).all() == []
    assert db.exec(select(Holding)).all() == []


def test_batch_rollback_on_oversell(client, db):
    sell = row(); sell.update(type='SELL', quantity=11)
    response = client.post('/portfolio/bulk-transaction', json=[row(), sell], headers=headers())
    assert response.status_code == 400
    assert db.exec(select(Transaction)).all() == []
    assert db.exec(select(Holding)).all() == []


@pytest.mark.parametrize('field,value', [('quantity',0),('quantity',-1),('price',0),('price',-1),('type','OTHER'),('symbol','  '),('quantity','NaN'),('price','Infinity')])
def test_invalid_inputs_leave_no_trade(client, db, field, value):
    payload = row(); payload[field] = value
    assert client.post('/portfolio/transaction', json=payload, headers=headers()).status_code == 422
    assert db.exec(select(Transaction)).all() == []


def test_normalization_retry_and_sale(client, db):
    payload = row(); payload['symbol'] = ' abc.ns '
    assert client.post('/portfolio/transaction', json=payload, headers=headers()).status_code == 200
    assert client.post('/portfolio/transaction', json=payload, headers=headers()).status_code == 200
    sell = row(); sell.update(type='SELL', quantity=5, price=150)
    assert client.post('/portfolio/transaction', json=sell, headers=headers()).status_code == 200
    holding = db.exec(select(Holding)).one()
    assert holding.symbol == 'ABC.NS'
    assert holding.quantity == 5
    assert holding.avg_price == 100
    assert len(db.exec(select(Transaction)).all()) == 2


def test_profile_does_not_expose_hash(client):
    response = client.post('/auth/profile', headers=headers(), json={'email':'owner@test.local','password':'','full_name':'Owner'})
    assert response.status_code == 200
    assert 'password' not in response.json()['user']
    assert 'HASH_NOT_FOR_RESPONSE' not in response.text


@pytest.mark.parametrize('secret', ['', 'short', 'finreview-local-dev-secret', 'change-me-for-hosted-deployments'])
def test_secret_must_be_configured(monkeypatch, secret):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    monkeypatch.setenv('AUTH_SECRET_KEY', secret)
    with pytest.raises(RuntimeError):
        main._auth_secret()


def test_future_and_expired_tokens_rejected(db):
    import hmac, hashlib
    for moment in (datetime.now(timezone.utc) + timedelta(days=1), datetime.now(timezone.utc) - timedelta(days=8)):
        payload = f'1:{int(moment.timestamp())}'
        signature = hmac.new(main._auth_secret(), payload.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(main.HTTPException) as exc:
            main._verify_token(f'Bearer frv1:{payload}:{signature}', db)
        assert exc.value.status_code == 401


def test_overlap_reports_unavailable_without_fake_values(db):
    for symbol in ('111111','222222'):
        db.add(Holding(user_id=1, symbol=symbol, quantity=5, avg_price=100, asset_type='MF'))
    db.commit()
    result = OverlapService(db).calculate_mf_overlap(1)
    assert result['status'] == 'unavailable'
    assert result['overlap_score'] is None
    assert result['top_shared_stocks'] == []


def test_database_rejects_duplicate_holdings(db):
    db.add(Holding(user_id=1, symbol='ABC', quantity=1, avg_price=1, asset_type='STOCK'))
    db.commit()
    db.add(Holding(user_id=1, symbol='ABC', quantity=1, avg_price=1, asset_type='STOCK'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_holding_failure_rolls_back_transaction(db, monkeypatch):
    from sqlalchemy import event
    def fail(mapper, connection, target):
        raise RuntimeError('injected holding failure')
    event.listen(Holding, 'before_insert', fail)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            PortablePortfolioService(db).add_transaction(1, 'ABC', 'BUY', 1, 100)
    finally:
        event.remove(Holding, 'before_insert', fail)
    assert db.exec(select(Transaction)).all() == []
    assert db.exec(select(Holding)).all() == []


def test_unauthenticated_and_wrong_user_single_rejected(client, db):
    assert client.post('/portfolio/transaction', json=row()).status_code == 401
    payload = row(); payload['user_id'] = 2
    assert client.post('/portfolio/transaction', json=payload, headers=headers()).status_code == 403
    assert db.exec(select(Transaction)).all() == []


def test_derived_refresh_failure_does_not_undo_trade(client, db, monkeypatch):
    monkeypatch.setattr(main, '_refresh_after_transactions', AsyncMock(return_value=False))
    response = client.post('/portfolio/transaction', json=row(), headers=headers())
    assert response.status_code == 200
    assert response.json()['analytics_updated'] is False
    assert len(db.exec(select(Transaction)).all()) == 1


def test_zero_balance_then_buy_resets_average(db):
    service = PortablePortfolioService(db)
    service.add_transaction(1, 'ABC', 'BUY', 10, 100)
    service.add_transaction(1, 'ABC', 'SELL', 10, 150)
    holding = service.add_transaction(1, 'ABC', 'BUY', 5, 200)
    assert holding.quantity == 5
    assert holding.avg_price == 200


def test_finite_overflow_rejected(db):
    with pytest.raises(ValueError):
        PortablePortfolioService(db).add_transaction(1, 'ABC', 'BUY', 1e308, 1e308)
    assert db.exec(select(Transaction)).all() == []
