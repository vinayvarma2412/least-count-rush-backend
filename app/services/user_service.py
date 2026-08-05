from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete
from sqlalchemy.exc import IntegrityError
from app.models.db_models import User
from datetime import datetime, timezone
import uuid

class UserService:
    async def get_user_by_firebase_id(self, db: AsyncSession, firebase_uid: str) -> User:
        result = await db.execute(
            select(User)
            .where(User.user_id == firebase_uid)
            .where(User.entity_active == True)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_user_by_idn(self, db: AsyncSession, user_idn: int) -> User:
        result = await db.execute(select(User).where(User.user_idn == user_idn).limit(1))
        return result.scalar_one_or_none()

    async def upsert_user(self, db: AsyncSession, firebase_uid: str, email: str = None, 
                          display_name: str = None, user_type: str = "authenticated") -> User:
        """
        Creates a new user or updates the last_active_date of an existing user.
        Also handles the case where the user's Firebase UID changed but email remains the same.
        """
        user = await self.get_user_by_firebase_id(db, firebase_uid)
        
        # If user not found by UID, but we have an email, check if email exists
        if not user and email:
            result = await db.execute(select(User).where(User.email == email).limit(1))
            user = result.scalar_one_or_none()
            if user:
                # The user exists with a different Firebase UID. Update it to the new one.
                user.user_id = firebase_uid

        now = datetime.now(timezone.utc)
        
        if user:
            # Update last active timestamp and other basic info if provided
            user.last_active_date = now
            if email and not user.email:
                user.email = email
            if display_name and not user.display_name:
                user.display_name = display_name
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user
        else:
            # Create new user
            username = f"user_{uuid.uuid4().hex[:8]}"
            
            new_user = User(
                user_id=firebase_uid,
                email=email,
                user_name=username,
                display_name=display_name,
                user_type=user_type,
                last_active_date=now
            )
            db.add(new_user)
            
            try:
                await db.commit()
                await db.refresh(new_user)
                return new_user
            except IntegrityError:
                await db.rollback()
                # Race condition: the user might have been created simultaneously by another request
                user = await self.get_user_by_firebase_id(db, firebase_uid)
                if user:
                    return user
                    
                if email:
                    result = await db.execute(select(User).where(User.email == email).limit(1))
                    user = result.scalar_one_or_none()
                    if user:
                        user.user_id = firebase_uid
                        db.add(user)
                        await db.commit()
                        await db.refresh(user)
                        return user
                        
                raise # Re-raise if we couldn't resolve the conflict

    async def update_user_profile(self, db: AsyncSession, user_idn: int, 
                                  display_name: str = None, avatar_seed: str = None):
        user = await self.get_user_by_idn(db, user_idn)
        if not user:
            return None
            
        user.last_active_date = datetime.now(timezone.utc)
            
        if display_name is not None:
            user.display_name = display_name
        if avatar_seed is not None:
            user.avatar_seed = avatar_seed
            
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def check_username_available(self, db: AsyncSession, username: str) -> bool:
        result = await db.execute(select(User).where(User.user_name == username).limit(1))
        return result.scalar_one_or_none() is None

    async def delete_user(self, db: AsyncSession, user_idn: int):
        user = await self.get_user_by_idn(db, user_idn)
        if user:
            from app.models.db_models import DeletedUser
            from datetime import timedelta

            now = datetime.now(timezone.utc)

            # ── Insert block record (for email-based re-registration block) ──
            if user.email:
                blocked_until = now + timedelta(days=15)
                deleted_record = DeletedUser(
                    email=user.email,
                    firebase_uid=user.user_id,
                    blocked_until=blocked_until
                )
                db.add(deleted_record)

            # ── Soft-delete: free the unique columns so a new row can claim them ──
            # Prefix user_name so it doesn't block future re-registration with same name
            old_name = user.user_name or f"{user.user_idn}"
            new_name = f"deleted_{user.user_idn}_{old_name}"[:63]

            user.entity_active = False
            user.user_id = None        # free the Firebase UID unique slot
            user.email = None          # free the email unique slot
            user.user_name = new_name  # free the username slot
            user.upd_dt = now

            db.add(user)
            await db.commit()
            return True
        return False

user_service = UserService()
