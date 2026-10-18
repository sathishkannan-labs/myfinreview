from sqlmodel import Session, select
from repositories.portable_repository import PortableHoldingRepository, PortableTransactionRepository
from services.portable_price_service import PortablePriceService
from models.portable_models import Holding, Transaction, PriceCache
from datetime import datetime
import hashlib

class PortablePortfolioService:
    def __init__(self, session: Session):
        self.holding_repo = PortableHoldingRepository(session)
        self.tx_repo = PortableTransactionRepository(session)
        self.price_service = PortablePriceService(session)

    def add_transaction(self, user_id: int, symbol: str, tx_type: str, quantity: float, price: float, date: datetime = None, name: str = None) -> Holding:
        return self.add_transactions(user_id, [dict(symbol=symbol, type=tx_type,
            quantity=quantity, price=price, date=date, name=name)])[0]

    def add_transactions(self, user_id: int, transactions: list[dict]) -> list[Holding]:
        """Persist a complete batch and its holdings together, or persist nothing.

        Lock the owning user first on PostgreSQL, including for new holdings, so
        concurrent requests for the same account cannot lose quantity updates.
        Repository methods that commit independently must not be used here.
        """
        from math import isfinite
        from models.portable_models import User, TransactionType
        session = self.holding_repo.session
        results = []
        try:
            if session.get_bind().dialect.name == 'sqlite':
                connection = session.connection()
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
            owner = session.exec(select(User).where(User.id == user_id)
                                 .with_for_update().execution_options(populate_existing=True)).first()
            if owner is None:
                raise ValueError("User not found")
            for tx in transactions:
                symbol = str(tx['symbol']).strip().upper()
                tx_type = tx['type']
                quantity, price = float(tx['quantity']), float(tx['price'])
                if not symbol or len(symbol) > 64:
                    raise ValueError("Invalid symbol")
                if tx_type not in ('BUY', 'SELL'):
                    raise ValueError("Transaction type must be BUY or SELL")
                if not isfinite(quantity) or quantity <= 0 or not isfinite(price) or price <= 0 or not isfinite(quantity * price):
                    raise ValueError("Quantity and price must be positive finite numbers")
                tx_date = tx.get('date') or datetime.now()
                # Preserve existing CSV retry semantics for this release.
                tx_hash = hashlib.md5(f"{user_id}:{tx_date:%Y-%m-%d}:{symbol}:{tx_type}:{quantity}:{price}".encode()).hexdigest()
                existing = session.exec(select(Transaction).where(Transaction.hash == tx_hash)).first()
                holding = session.exec(select(Holding).where(
                    Holding.user_id == user_id, Holding.symbol == symbol)
                    .execution_options(populate_existing=True)).first()
                if existing:
                    if holding is None:
                        raise ValueError("Existing transaction has no holding; reconciliation required")
                    results.append(holding)
                    continue
                if holding is None:
                    holding = Holding(user_id=user_id, symbol=symbol, quantity=0,
                        avg_price=0, asset_type='MF' if symbol.isdigit() else 'STOCK')
                if tx_type == 'SELL' and quantity > holding.quantity:
                    raise ValueError(f"Insufficient holdings for {symbol}")
                if tx_type == 'BUY':
                    new_quantity = holding.quantity + quantity
                    new_average = (holding.quantity * holding.avg_price + quantity * price) / new_quantity
                else:
                    new_quantity = holding.quantity - quantity
                    new_average = holding.avg_price if new_quantity else 0.0
                if not isfinite(new_quantity) or not isfinite(new_average):
                    raise ValueError("Transaction exceeds supported numeric range")
                holding.quantity, holding.avg_price = new_quantity, new_average
                holding.last_updated = datetime.now()
                session.add(holding)
                session.add(Transaction(user_id=user_id, symbol=symbol, type=TransactionType(tx_type),
                    quantity=quantity, price=price, date=tx_date, hash=tx_hash))
                name = tx.get('name')
                if name and name.strip().upper() != symbol:
                    cached = session.get(PriceCache, symbol)
                    if cached:
                        cached.name = name
                    else:
                        from datetime import timedelta
                        session.add(PriceCache(symbol=symbol, price=0, name=name,
                            timestamp=datetime.now() - timedelta(days=1)))
                session.flush()
                results.append(holding)
            session.commit()
            return results
        except Exception:
            session.rollback()
            raise

    async def get_portfolio_summary(self, user_id: int):
        holdings = self.holding_repo.get_user_holdings(user_id)
        if not holdings:
            return {'total_valuation': 0, 'total_cost': 0, 'total_gain_loss': 0, 'total_gain_loss_pct': 0, 'holdings': []}

        symbols = [h.symbol for h in holdings]
        price_map = await self.price_service.get_prices_for_symbols(symbols)
        
        statement = select(PriceCache).where(PriceCache.symbol.in_(symbols))
        metadata_items = self.holding_repo.session.exec(statement).all()
        name_map = {m.symbol: m.name for m in metadata_items if m.name}

        results = []
        total_valuation = 0.0
        total_cost = 0.0

        for holding in holdings:
            symbol = holding.symbol
            current_price = price_map.get(symbol)
            asset_name = name_map.get(symbol) or symbol
            
            h_data = {
                "symbol": symbol,
                "name": asset_name,
                "total_quantity": holding.quantity,
                "avg_price": holding.avg_price,
                "asset_type": holding.asset_type
            }
            
            if current_price and current_price > 0:
                h_data['current_price'] = current_price
                h_data['current_valuation'] = float(holding.quantity) * current_price
                h_cost = float(holding.quantity) * float(holding.avg_price)
                h_data['gain_loss'] = h_data['current_valuation'] - h_cost
                h_data['gain_loss_pct'] = (h_data['gain_loss'] / h_cost * 100) if h_cost > 0 else 0
                
                total_valuation += h_data['current_valuation']
                total_cost += h_cost
            
            results.append(h_data)
        
        return {
            'total_valuation': total_valuation,
            'total_cost': total_cost,
            'total_gain_loss': total_valuation - total_cost,
            'total_gain_loss_pct': ((total_valuation - total_cost) / total_cost * 100) if total_cost > 0 else 0,
            'holdings': results
        }
