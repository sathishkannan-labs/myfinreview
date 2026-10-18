from typing import Any, Dict
from sqlmodel import Session, select
from models.portable_models import Holding


class OverlapService:
    def __init__(self, session: Session):
        self.session = session

    def calculate_mf_overlap(self, user_id: int) -> Dict[str, Any]:
        """Do not invent exposure figures without dated underlying holdings."""
        funds = self.session.exec(select(Holding).where(
            Holding.user_id == user_id, Holding.asset_type == 'MF', Holding.quantity > 0)).all()
        enough_funds = len(funds) >= 2
        return {
            "status": "unavailable" if enough_funds else "insufficient_funds",
            "is_simulated": False,
            "overlap_score": None,
            "severity": None,
            "top_shared_stocks": [],
            "diagnostic_summary": (
                "Overlap is unavailable: verified, dated underlying fund holdings are not connected."
                if enough_funds else "Add at least two mutual funds to enable overlap analysis once holdings data is connected."
            ),
            "impact_alert": None,
        }
