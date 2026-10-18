"""In-app purchase verification and transaction history."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_admin_api_key, get_current_db_user
from app.database import get_db_session
from app.models.db_models import User
from app.services import iap_service

router = APIRouter(prefix="/api/iap", tags=["IAP"])
admin_router = APIRouter(
    prefix="/api/admin/iap",
    tags=["Admin - IAP"],
    dependencies=[Depends(get_admin_api_key)],
)


class VerifyPurchaseRequest(BaseModel):
    platform: str = Field(..., description="android or ios")
    product_id: str
    plan_id: Optional[str] = None
    purchase_id: Optional[str] = None
    verification_data: str
    source: str = "purchase"


class VerifyPurchaseResponse(BaseModel):
    verified: bool
    already_recorded: bool
    ads_free: bool
    ads_free_until: Optional[datetime]
    product_id: str
    plan_id: Optional[str]
    transaction_idn: int
    expires_dt: Optional[datetime]
    store_transaction_id: str


class IapTransactionResponse(BaseModel):
    iap_transaction_idn: int
    user_idn: int
    platform: str
    product_id: str
    plan_id: Optional[str]
    store_transaction_id: str
    original_transaction_id: Optional[str]
    verification_status: str
    source: str
    environment: Optional[str]
    purchase_dt: Optional[datetime]
    expires_dt: Optional[datetime]
    acknowledged: bool
    error_message: Optional[str]
    crt_dt: Optional[datetime]

    class Config:
        from_attributes = True


@router.post("/verify", response_model=VerifyPurchaseResponse)
async def verify_purchase(
    req: VerifyPurchaseRequest,
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await iap_service.verify_and_record(
            db,
            current_user,
            platform=req.platform.lower(),
            product_id=req.product_id,
            plan_id_hint=req.plan_id,
            purchase_id=req.purchase_id,
            verification_data=req.verification_data,
            source=req.source.lower(),
        )
        return VerifyPurchaseResponse(**result)
    except iap_service.IapVerificationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.http_detail())


@router.get("/transactions", response_model=list[IapTransactionResponse])
async def my_transactions(
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await iap_service.list_user_transactions(db, current_user.user_idn)
    return rows


@admin_router.get("/transactions", response_model=list[IapTransactionResponse])
async def admin_list_transactions(
    user_idn: Optional[int] = Query(None),
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    return await iap_service.list_all_transactions(
        db,
        user_idn=user_idn,
        platform=platform,
        status=status,
        limit=limit,
    )
