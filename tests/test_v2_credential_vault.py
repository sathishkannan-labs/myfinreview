import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.credential_vault import (
    BrokerConnection, disconnect, read_trusted, status, store_trusted
)

@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY",Fernet.generate_key().decode())
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(email="vault1@example.com",password="x"))
        db.add(User(email="vault2@example.com",password="x"))
        db.commit()
        yield db

def test_encrypted_isolation_and_public_status(session):
    store_trusted(session,1,"fyers","quotes",{"access_token":"MY_PRIVATE_TOKEN"})
    row=session.exec(select(BrokerConnection)).one()
    assert "MY_PRIVATE_TOKEN" not in row.encrypted_credentials
    assert read_trusted(session,1,"fyers","quotes")["access_token"]=="MY_PRIVATE_TOKEN"
    assert "MY_PRIVATE_TOKEN" not in str(status(session,1))
    assert status(session,2)["connections"]==[]
    with pytest.raises(ValueError):
        read_trusted(session,2,"fyers","quotes")

def test_scope_binding_resists_ciphertext_copy(session):
    store_trusted(session,1,"upstox","historical",{"access_token":"sensitive"})
    row=session.exec(select(BrokerConnection)).one()
    row.user_id=2
    session.add(row)
    session.commit()
    with pytest.raises(RuntimeError,match="scope mismatch"):
        read_trusted(session,2,"upstox","historical")

def test_expired_access_rejected(session):
    store_trusted(session,1,"zerodha","quotes",{"token":"expiring"},
                  expires_at=datetime.now(timezone.utc)-timedelta(minutes=1))
    assert status(session,1)["connections"][0]["state"]=="EXPIRED"
    with pytest.raises(ValueError,match="expired"):
        read_trusted(session,1,"zerodha","quotes")

def test_disconnect_deletes_own_scoped_broker_only(session):
    store_trusted(session,1,"fyers","quotes",{"token":"a"})
    store_trusted(session,1,"fyers","historical",{"token":"b"})
    store_trusted(session,2,"fyers","quotes",{"token":"c"})
    store_trusted(session,1,"zerodha","quotes",{"token":"d"})
    result=disconnect(session,1,"fyers")
    assert result["local_credentials_deleted"] is True
    assert result["upstream_revocation"]=="NOT_PERFORMED"
    assert len(status(session,1)["connections"])==1
    assert len(status(session,2)["connections"])==1

def test_missing_or_wrong_key_fails_closed(session,monkeypatch):
    store_trusted(session,1,"fyers","quotes",{"token":"test"})
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY",Fernet.generate_key().decode())
    with pytest.raises(RuntimeError,match="unavailable"):
        read_trusted(session,1,"fyers","quotes")
    monkeypatch.delenv("FINREVIEW_BROKER_VAULT_KEY")
    with pytest.raises(RuntimeError,match="disabled"):
        read_trusted(session,1,"fyers","quotes")

def test_reject_bad_scope(session):
    with pytest.raises(ValueError):
        store_trusted(session,1,"unknown","quotes",{"token":"x"})
    with pytest.raises(ValueError):
        store_trusted(session,1,"fyers","quotes",{"token":""})
