import sys
from pathlib import Path
from types import SimpleNamespace
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from models.portable_models import User
from intelligence.routes import broker_connection_status, disconnect_broker
from intelligence.credential_vault import store_trusted

def test_connection_routes_return_no_secrets_and_disconnect_only_self(monkeypatch):
    monkeypatch.setenv("FINREVIEW_BROKER_VAULT_KEY",Fernet.generate_key().decode())
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(email="conn1@example.com",password="x"))
        db.add(User(email="conn2@example.com",password="x"))
        db.commit()
        store_trusted(db,1,"fyers","quotes",{"token":"hidden1"})
        store_trusted(db,2,"fyers","quotes",{"token":"hidden2"})
        u1=SimpleNamespace(id=1)
        u2=SimpleNamespace(id=2)
        assert len(broker_connection_status(user=u1,session=db)["connections"])==1
        assert "hidden1" not in str(broker_connection_status(user=u1,session=db))
        result=disconnect_broker("fyers",user=u1,session=db)
        assert result["local_credentials_deleted"] is True
        assert result["upstream_revocation"]=="NOT_PERFORMED"
        assert broker_connection_status(user=u1,session=db)["connections"]==[]
        assert len(broker_connection_status(user=u2,session=db)["connections"])==1
