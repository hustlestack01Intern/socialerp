"""
Identity Linking Service

Links WhatsApp, Instagram, and TikTok identities to unified CRM customers
with explicit consent evidence per Kenya DPA requirements.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain import TenantId
from packages.core.models.crm import CustomerIdentity
from packages.core.models.instagram import InstagramContact
from packages.core.models.order import Customer
from packages.core.models.whatsapp import WhatsAppContact


class IdentityLinkingService:
    """
    Manages cross-channel identity linking for CRM customers.

    All links require explicit consent evidence per Kenya DPA.
    Supports WhatsApp, Instagram, and TikTok channels.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditLogger(session)

    # =============================================================================
    # Core Linking Methods
    # =============================================================================

    async def link_identities(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        channel: str,  # "whatsapp" | "instagram" | "tiktok"
        channel_identity: str,
        channel_identity_type: str,  # "phone" | "handle" | "user_id"
        consent_source: str,  # "owner_linked" | "customer_provided" | "oauth_flow"
        evidence: dict[str, Any] | None = None,
        is_primary: bool = True,
    ) -> CustomerIdentity:
        """
        Link a channel identity to a unified CRM customer.

        Args:
            tenant_id: Tenant ID
            customer_id: Unified CRM customer ID
            channel: "whatsapp" | "instagram" | "tiktok"
            channel_identity: Phone, handle, or user ID
            channel_identity_type: "phone" | "handle" | "user_id"
            consent_source: "owner_linked" | "customer_provided" | "oauth_flow"
            evidence: Additional consent evidence (message hash, OAuth token hash, etc.)
            is_primary: Whether this is the primary identity for the channel

        Returns:
            Created CustomerIdentity record

        Raises:
            ValueError: If channel is invalid or link already exists
        """
        valid_channels = {"whatsapp", "instagram", "tiktok"}
        if channel not in valid_channels:
            raise ValueError(f"Invalid channel: {channel}. Must be one of {valid_channels}")

        if channel == "whatsapp" and channel_identity_type != "phone":
            raise ValueError("WhatsApp channel must use 'phone' identity type")
        if channel == "instagram" and channel_identity_type != "handle":
            raise ValueError("Instagram channel must use 'handle' identity type")
        if channel == "tiktok" and channel_identity_type != "user_id":
            raise ValueError("TikTok channel must use 'user_id' identity type")

        # Check if link already exists
        result = await self.session.execute(
            select(CustomerIdentity).where(
                CustomerIdentity.tenant_id == tenant_id,
                CustomerIdentity.customer_id == customer_id,
                CustomerIdentity.channel == channel,
                CustomerIdentity.channel_identity == channel_identity,
            ),
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise ValueError(
                f"Identity link already exists for {channel}:{channel_identity}",
            )

        # If this is primary, unset any existing primary for this channel
        if True:  # is_primary:
            from sqlalchemy import update

            await self.session.execute(
                update(CustomerIdentity)
                .where(
                    CustomerIdentity.tenant_id == tenant_id,
                    CustomerIdentity.customer_id == customer_id,
                    CustomerIdentity.channel == channel,
                )
                .values(is_primary=False),
            )

        # Create identity link
        identity = CustomerIdentity(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel=channel,
            channel_identity=channel_identity,
            channel_identity_type=channel_identity_type,
            consent_given_at=datetime.now(UTC),
            consent_source=consent_source,
            evidence_json=evidence or {},
            is_primary=True,
        )

        self.session.add(identity)
        await self.session.flush()

        # Audit
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="link_identity",
            resource="customer_identity",
            resource_id=identity.id,
            after={
                "channel": channel,
                "channel_identity": channel_identity,
                "consent_source": consent_source,
            },
        )

        return identity

    async def get_unified_customer(
        self, tenant_id: TenantId, channel: str, channel_identity: str,
    ) -> Customer | None:
        """
        Resolve a channel identity to a unified CRM customer.

        Args:
            tenant_id: Tenant ID
            channel: "whatsapp" | "instagram" | "tiktok"
            channel_identity: Phone, handle, or user ID

        Returns:
            Unified Customer if linked, None otherwise
        """
        result = await self.session.execute(
            select(CustomerIdentity).where(
                CustomerIdentity.tenant_id == tenant_id,
                CustomerIdentity.channel == channel,
                CustomerIdentity.channel_identity == channel_identity,
            ),
        )
        identity = result.scalar_one_or_none()
        if not identity:
            return None
        # Fetch the customer separately
        from packages.core.models.order import Customer as CustomerModel

        result = await self.session.execute(
            select(CustomerModel).where(
                CustomerModel.id == identity.customer_id,
                CustomerModel.tenant_id == tenant_id,
            ),
        )
        customer_obj: CustomerModel | None = result.scalar_one_or_none()  # type: ignore[assignment]
        return customer_obj

    async def get_all_identities(
        self, tenant_id: TenantId, customer_id: UUID,
    ) -> list[CustomerIdentity]:
        """Get all channel identities for a customer."""
        result = await self.session.execute(
            select(CustomerIdentity).where(
                CustomerIdentity.tenant_id == tenant_id,
                CustomerIdentity.customer_id == customer_id,
            ).order_by(CustomerIdentity.channel, CustomerIdentity.channel_identity),
        )
        return list(result.scalars().all())

    async def unlink_identity(
        self, tenant_id: TenantId, identity_id: UUID,
    ) -> bool:
        """
        Unlink a channel identity from a customer (withdraw consent).

        Returns True if unlinked, False if not found.
        """
        result = await self.session.execute(
            select(CustomerIdentity).where(
                CustomerIdentity.tenant_id == tenant_id,
                CustomerIdentity.id == identity_id,
            ),
        )
        identity = result.scalar_one_or_none()
        if not identity:
            return False

        await self.session.delete(identity)
        await self.session.flush()

        # Audit
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="unlink_identity",
            resource="customer_identity",
            resource_id=identity_id,
            before={
                "channel": identity.channel,
                "channel_identity": identity.channel_identity,
            },
        )

        return True

    # =============================================================================
    # Channel-Specific Helpers
    # =============================================================================

    async def link_whatsapp(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        phone: str,
        consent_source: str,
        evidence: dict[str, Any] | None = None,
    ) -> CustomerIdentity:
        """Link a WhatsApp phone number to a customer."""
        return await self.link_identities(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel="whatsapp",
            channel_identity=phone,
            channel_identity_type="phone",
            consent_source=consent_source,
            evidence=evidence,
        )

    async def link_instagram(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        ig_handle: str,
        consent_source: str,
        evidence: dict[str, Any] | None = None,
    ) -> CustomerIdentity:
        """Link an Instagram handle to a customer."""
        return await self.link_identities(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel="instagram",
            channel_identity=ig_handle,
            channel_identity_type="handle",
            consent_source=consent_source,
            evidence=evidence,
        )

    async def link_tiktok(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        tiktok_user_id: str,
        consent_source: str,
        evidence: dict[str, Any] | None = None,
    ) -> CustomerIdentity:
        """Link a TikTok user ID to a customer."""
        return await self.link_identities(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel="tiktok",
            channel_identity=tiktok_user_id,
            channel_identity_type="user_id",
            consent_source=consent_source,
            evidence=evidence,
        )

    async def resolve_whatsapp(
        self, tenant_id: TenantId, phone: str,
    ) -> Customer | None:
        """Resolve WhatsApp phone to unified customer."""
        return await self.get_unified_customer(tenant_id, "whatsapp", phone)

    async def resolve_instagram(
        self, tenant_id: TenantId, ig_handle: str,
    ) -> Customer | None:
        """Resolve Instagram handle to unified customer."""
        return await self.get_unified_customer(tenant_id, "instagram", ig_handle)

    async def resolve_tiktok(
        self, tenant_id: TenantId, tiktok_user_id: str,
    ) -> Customer | None:
        """Resolve TikTok user ID to unified customer."""
        return await self.get_unified_customer(tenant_id, "tiktok", tiktok_user_id)

    # =============================================================================
    # Bulk Linking (for onboarding)
    # =============================================================================

    async def bulk_link_from_onboarding(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        whatsapp_phone: str | None = None,
        instagram_handle: str | None = None,
        tiktok_user_id: str | None = None,
        consent_source: str = "owner_linked",
    ) -> dict[str, CustomerIdentity]:
        """
        Bulk link identities during onboarding wizard step 2.

        Returns dict of created links by channel.
        """
        results = {}

        if whatsapp_phone:
            results["whatsapp"] = await self.link_whatsapp(
                tenant_id, customer_id, whatsapp_phone, consent_source,
            )

        if instagram_handle:
            results["instagram"] = await self.link_instagram(
                tenant_id, customer_id, instagram_handle, consent_source,
            )

        if tiktok_user_id:
            results["tiktok"] = await self.link_tiktok(
                tenant_id, customer_id, tiktok_user_id, consent_source,
            )

        return results

    # =============================================================================
    # Resolution via Contact Models
    # =============================================================================

    async def resolve_from_whatsapp_contact(
        self, tenant_id: TenantId, whatsapp_contact: WhatsAppContact,
    ) -> Customer | None:
        """Resolve customer from WhatsApp contact."""
        return await self.resolve_whatsapp(tenant_id, whatsapp_contact.phone)

    async def resolve_from_instagram_contact(
        self, tenant_id: TenantId, instagram_contact: InstagramContact,
    ) -> Customer | None:
        """Resolve customer from Instagram contact."""
        if not instagram_contact.ig_handle:
            return None
        return await self.resolve_instagram(tenant_id, instagram_contact.ig_handle)

    async def link_from_whatsapp_contact(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        whatsapp_contact: WhatsAppContact,
        consent_source: str = "customer_provided",
    ) -> CustomerIdentity:
        """Link customer from WhatsApp contact."""
        return await self.link_whatsapp(
            tenant_id,
            customer_id,
            whatsapp_contact.phone,
            consent_source,
            evidence={
                "wa_id": whatsapp_contact.wa_id,
                "profile_name": whatsapp_contact.name,
            },
        )

    async def link_from_instagram_contact(
        self,
        tenant_id: TenantId,
        customer_id: UUID,
        instagram_contact: InstagramContact,
        consent_source: str = "customer_provided",
    ) -> CustomerIdentity:
        """Link customer from Instagram contact."""
        if not instagram_contact.ig_handle:
            raise ValueError("Instagram contact has no handle")
        return await self.link_instagram(
            tenant_id,
            customer_id,
            instagram_contact.ig_handle,
            consent_source,
            evidence={
                "ig_scoped_user_id": instagram_contact.ig_scoped_user_id,
                "profile_name": instagram_contact.name,
            },
        )
