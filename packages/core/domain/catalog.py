"""
Catalog Domain Models

Value objects for catalog domain: Product, Variant, Category, Price.
All invariants enforced at type level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from packages.core.domain import (
    SKU,
    Money,
    Quantity,
    TenantId,
)

# =============================================================================
# Enums
# =============================================================================


class ProductStatus(str, Enum):
    """Product lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    HIDDEN = "hidden"
    DISCONTINUED = "discontinued"


class VariantAttributeType(str, Enum):
    """Types of variant attributes."""

    COLOR = "color"
    SIZE = "size"
    MATERIAL = "material"
    STYLE = "style"
    CAPACITY = "capacity"
    CUSTOM = "custom"


# =============================================================================
# Value Objects
# =============================================================================


@dataclass(frozen=True)
class VariantAttribute:
    """A single variant attribute (e.g., color=red, size=M)."""

    type: VariantAttributeType
    value: str
    display_name: str | None = None  # Optional human-readable name

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("Variant attribute value cannot be empty")
        object.__setattr__(self, "value", self.value.strip())
        if self.display_name:
            object.__setattr__(self, "display_name", self.display_name.strip())


@dataclass(frozen=True)
class ProductVariant:
    """A product variant with specific attributes."""

    sku: SKU
    cost_price: Money
    sell_price: Money
    attributes: tuple[VariantAttribute, ...] = field(default_factory=tuple)
    quantity: Quantity = field(default_factory=lambda: Quantity(0))
    reorder_level: Quantity = field(default_factory=lambda: Quantity(5))
    supplier_id: UUID | None = None
    location_id: UUID | None = None
    is_active: bool = True
    id: UUID = field(default_factory=uuid4)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate prices
        if self.cost_price.cents < 0 or self.sell_price.cents < 0:
            raise ValueError("Prices cannot be negative")
        if self.sell_price < self.cost_price:
            raise ValueError("Sell price cannot be less than cost price")

        # Validate attributes
        if not self.attributes:
            raise ValueError("Variant must have at least one attribute")

        # Check for duplicate attribute types
        attr_types = [a.type for a in self.attributes]
        if len(attr_types) != len(set(attr_types)):
            raise ValueError("Duplicate attribute types not allowed")

    @property
    def is_in_stock(self) -> bool:
        return self.quantity.value > 0

    @property
    def is_low_stock(self) -> bool:
        return self.quantity <= self.reorder_level

    @property
    def profit_margin(self) -> Money:
        return self.sell_price - self.cost_price

    def with_quantity(self, new_quantity: Quantity) -> ProductVariant:
        """Return new variant with updated quantity (immutable)."""
        return ProductVariant(
            id=self.id,
            sku=self.sku,
            attributes=self.attributes,
            cost_price=self.cost_price,
            sell_price=self.sell_price,
            quantity=new_quantity,
            reorder_level=self.reorder_level,
            supplier_id=self.supplier_id,
            location_id=self.location_id,
            is_active=self.is_active,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class Product:
    """Product aggregate root."""

    tenant_id: TenantId
    name: str
    id: UUID = field(default_factory=uuid4)
    description: str = ""
    base_sku: SKU | None = None  # Optional base SKU for the product line
    category_id: UUID | None = None
    brand: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    variants: tuple[ProductVariant, ...] = field(default_factory=tuple)
    status: ProductStatus = ProductStatus.DRAFT
    images: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: "")  # Will be set by DB
    updated_at: str = field(default_factory=lambda: "")  # Will be set by DB

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Product name cannot be empty")
        object.__setattr__(self, "name", self.name.strip())

        if self.variants:
            # Validate unique SKUs across variants
            variant_skus = [v.sku for v in self.variants]
            if len(variant_skus) != len(set(variant_skus)):
                raise ValueError("Duplicate variant SKUs not allowed")

            # Validate all variants have same attribute types
            if len(self.variants) > 1:
                first_attrs = {(a.type, a.value) for a in self.variants[0].attributes}
                for v in self.variants[1:]:
                    v_attrs = {(a.type, a.value) for a in v.attributes}
                    if v_attrs != first_attrs:
                        raise ValueError("All variants must have same attribute types")

    @property
    def total_quantity(self) -> Quantity:
        return sum((v.quantity for v in self.variants), Quantity(0))

    @property
    def is_in_stock(self) -> bool:
        return any(v.is_in_stock for v in self.variants)

    @property
    def lowest_price(self) -> Money | None:
        if not self.variants:
            return None
        return min(v.sell_price for v in self.variants)

    @property
    def highest_price(self) -> Money | None:
        if not self.variants:
            return None
        return max(v.sell_price for v in self.variants)

    def get_variant_by_sku(self, sku: SKU) -> ProductVariant | None:
        for v in self.variants:
            if v.sku == sku:
                return v
        return None

    def get_variant_by_attributes(
        self, attributes: dict[VariantAttributeType, str],
    ) -> ProductVariant | None:
        """Find variant matching all given attributes."""
        for v in self.variants:
            matches = True
            for attr_type, value in attributes.items():
                if not any(a.type == attr_type and a.value == value for a in v.attributes):
                    matches = False
                    break
            if matches:
                return v
        return None

    def with_status(self, new_status: ProductStatus) -> Product:
        return Product(
            id=self.id,
            tenant_id=self.tenant_id,
            name=self.name,
            description=self.description,
            base_sku=self.base_sku,
            category_id=self.category_id,
            brand=self.brand,
            tags=self.tags,
            variants=self.variants,
            status=new_status,
            images=self.images,
            metadata=self.metadata,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(frozen=True)
class Category:
    """Product category."""

    tenant_id: TenantId
    name: str
    id: UUID = field(default_factory=uuid4)
    description: str = ""
    parent_id: UUID | None = None  # For hierarchical categories
    is_active: bool = True
    sort_order: int = 0
    image_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Category name cannot be empty")
        object.__setattr__(self, "name", self.name.strip())


# =============================================================================
# Input Models (for API requests)
# =============================================================================


@dataclass(frozen=True)
class CreateProductRequest:
    """Input for creating a product."""

    name: str
    description: str = ""
    base_sku: SKU | None = None
    category_id: UUID | None = None
    brand: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    images: tuple[str, ...] = field(default_factory=tuple)
    variants: tuple[CreateVariantRequest, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CreateVariantRequest:
    """Input for creating a variant."""

    sku: SKU
    attributes: tuple[VariantAttribute, ...]
    cost_price_cents: int
    sell_price_cents: int
    quantity: int = 0
    reorder_level: int = 5
    supplier_id: UUID | None = None
    location_id: UUID | None = None


@dataclass(frozen=True)
class UpdateProductRequest:
    """Input for updating a product."""

    name: str | None = None
    description: str | None = None
    base_sku: SKU | None = None
    category_id: UUID | None = None
    brand: str | None = None
    tags: tuple[str, ...] | None = None
    images: tuple[str, ...] | None = None
    status: ProductStatus | None = None


@dataclass(frozen=True)
class UpdateVariantRequest:
    """Input for updating a variant."""

    cost_price_cents: int | None = None
    sell_price_cents: int | None = None
    quantity: int | None = None
    reorder_level: int | None = None
    supplier_id: UUID | None = None
    location_id: UUID | None = None
    is_active: bool | None = None


@dataclass(frozen=True)
class CreateCategoryRequest:
    """Input for creating a category."""

    name: str
    description: str = ""
    parent_id: UUID | None = None
    is_active: bool = True
    sort_order: int = 0
    image_url: str = ""


@dataclass(frozen=True)
class UpdateCategoryRequest:
    """Input for updating a category."""

    name: str | None = None
    description: str | None = None
    parent_id: UUID | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    image_url: str | None = None
