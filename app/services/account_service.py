from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.time_utils import local_today
from app.models.entities import Account, DeliveryRecord, DeliveryStatus, Platform
from app.schemas.common import AccountCreate, AccountUpdate


def _profile_dir(platform: Platform, account_id: int) -> str:
    return f"browser_profiles/{platform.value}/account_{account_id}"


def _attach_stats(db: Session, account: Account) -> dict:
    today = local_today()
    total_delivered_count = (
        db.scalar(
            select(func.count(DeliveryRecord.id)).where(
                DeliveryRecord.account_id == account.id,
                DeliveryRecord.status == DeliveryStatus.DELIVERED.value,
            )
        )
        or 0
    )
    today_delivered_count = (
        db.scalar(
            select(func.count(DeliveryRecord.id)).where(
                DeliveryRecord.account_id == account.id,
                DeliveryRecord.status == DeliveryStatus.DELIVERED.value,
                func.date(DeliveryRecord.created_at) == str(today),
            )
        )
        or 0
    )
    return {
        "id": account.id,
        "platform": account.platform,
        "phone": account.phone,
        "username": account.username,
        "remark": account.remark,
        "is_default": account.is_default,
        "is_active": account.is_active,
        "profile_dir": account.profile_dir,
        "today_delivered_count": today_delivered_count,
        "total_delivered_count": total_delivered_count,
    }


def list_accounts(db: Session, platform: Platform | None = None) -> list[dict]:
    stmt = select(Account)
    if platform:
        stmt = stmt.where(Account.platform == platform.value)
    stmt = stmt.order_by(Account.platform, Account.is_default.desc(), Account.id)
    return [_attach_stats(db, account) for account in db.scalars(stmt)]


def create_account(db: Session, payload: AccountCreate) -> dict:
    if payload.is_default:
        _clear_default(db, payload.platform)
    account = Account(
        platform=payload.platform.value,
        phone=payload.phone,
        username=payload.username,
        remark=payload.remark,
        is_default=payload.is_default,
        is_active=payload.is_active,
        profile_dir="",
    )
    db.add(account)
    db.flush()
    account.profile_dir = _profile_dir(payload.platform, account.id)
    db.commit()
    db.refresh(account)
    return _attach_stats(db, account)


def update_account(db: Session, account_id: int, payload: AccountUpdate) -> dict | None:
    account = db.get(Account, account_id)
    if not account:
        return None
    if payload.is_default:
        _clear_default(db, payload.platform, exclude_id=account_id)
    account.platform = payload.platform.value
    account.phone = payload.phone
    account.username = payload.username
    account.remark = payload.remark
    account.is_default = payload.is_default
    account.is_active = payload.is_active
    account.profile_dir = _profile_dir(payload.platform, account.id)
    db.commit()
    db.refresh(account)
    return _attach_stats(db, account)


def set_default_account(db: Session, account_id: int) -> dict | None:
    account = db.get(Account, account_id)
    if not account:
        return None
    _clear_default(db, Platform(account.platform), exclude_id=account.id)
    account.is_default = True
    db.commit()
    db.refresh(account)
    return _attach_stats(db, account)


def get_account(db: Session, account_id: int) -> Account | None:
    return db.get(Account, account_id)


def _clear_default(db: Session, platform: Platform, exclude_id: int | None = None) -> None:
    stmt = select(Account).where(Account.platform == platform.value, Account.is_default.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(Account.id != exclude_id)
    for account in db.scalars(stmt):
        account.is_default = False
