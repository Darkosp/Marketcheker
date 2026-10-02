"""Читања на ценовници и поединечни редови со цени."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import PromoType, RunStatus

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.chain import Store


class PricelistRun(Base, TimestampMixin):
    """Еден обид да се прочита еден ценовник.

    Секогаш се запишува - и кога успее и кога падне. Правило: читач што не
    успее НИКОГАШ не враќа тивок празен резултат; status и error_message
    кажуваат што се случило.
    """

    __tablename__ = "pricelist_run"
    __table_args__ = (
        Index("ix_pricelist_run_store_date", "store_id", "run_date"),
        Index("ix_pricelist_run_chain_date", "chain_id", "run_date"),
        Index(
            "ix_pricelist_run_status_date",
            "status",
            "run_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chain.id", ondelete="CASCADE"))
    # NULL = читање на ниво на синџир (пр. откривање на листата продавници).
    store_id: Mapped[int | None] = mapped_column(
        ForeignKey("store.id", ondelete="CASCADE")
    )

    # Датумот на дневното читање (Europe/Skopje), клуч за „денешни попусти".
    run_date: Mapped[date] = mapped_column(Date, index=True)
    # Датумот напишан во заглавието на самиот ценовник. Ако се разликува од
    # run_date, маркетот не го освежил - вреди да се види во извештај.
    pricelist_date: Mapped[date | None] = mapped_column(Date)

    source_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[RunStatus] = mapped_column(
        String(24), default=RunStatus.RUNNING, server_default="running", index=True
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rows_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_discount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Хеш од преземената содржина: ако е ист како претходниот успешен run,
    # ценовникот е непроменет и не мора да се парсира повторно.
    content_hash: Mapped[str | None] = mapped_column(String(64))

    error_type: Mapped[str | None] = mapped_column(String(96))
    error_message: Mapped[str | None] = mapped_column(Text)

    store: Mapped[Store | None] = relationship(back_populates="runs")
    price_rows: Mapped[list[PriceRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<PricelistRun {self.id} store={self.store_id} {self.status}>"


class PriceRow(Base):
    """Еден ред од ценовник на една продавница, од едно читање.

    Колоните ги следат заглавијата на изворите: назив, продажна цена,
    единечна цена, достапност, опис, редовна цена, цена со попуст, попуст %,
    вид на продажно поттикнување, времетраење.
    """

    __tablename__ = "price_row"
    __table_args__ = (
        # Главното прашање на апликацијата: денешните попусти во одбрани
        # продавници. Партиалниот индекс го држи малку бидејќи попустите се
        # мал дел од ценовникот.
        Index(
            "ix_price_row_discounts",
            "store_id",
            "run_date",
            postgresql_where=text("is_discount"),
        ),
        Index("ix_price_row_run", "run_id"),
        Index("ix_price_row_product_date", "product_id", "run_date"),
        CheckConstraint(
            "discount_pct IS NULL OR (discount_pct >= 0 AND discount_pct <= 100)",
            name="discount_pct_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pricelist_run.id", ondelete="CASCADE")
    )
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    # Денормализирано од run-от: го штеди JOIN-от на најчестото прашање.
    run_date: Mapped[date] = mapped_column(Date)

    # ---- Цени во денари ----
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # продажна
    regular_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # редовна
    discount_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # со попуст
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    # Единечна цена како што ја дава ценовникот (различни извори, различна
    # единица), и наша пресметана за kg/l - за „каде е најевтино".
    unit_price_raw: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    unit_price_base: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    # ---- Вид на поттикнување ----
    promo_type: Mapped[PromoType] = mapped_column(
        String(16), default=PromoType.NONE, server_default="none"
    )
    # Оригиналниот текст („АКЦИСКА ПРОДАЖБА", „ЛОЈАЛНОСТ", ...) за да не
    # изгубиме информација кога мапирањето не е сигурно.
    promo_type_raw: Mapped[str | None] = mapped_column(String(255))

    # ---- Времетраење ----
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    # Еднодневен попуст: датум од == датум до.
    is_single_day: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Попуст = ред со пополнета цена со попуст.
    is_discount: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    availability: Mapped[str | None] = mapped_column(String(96))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    run: Mapped[PricelistRun] = relationship(back_populates="price_rows")
    store: Mapped[Store] = relationship(back_populates="price_rows")
    product: Mapped[Product] = relationship(back_populates="price_rows")

    def __repr__(self) -> str:
        return f"<PriceRow {self.id} product={self.product_id} {self.discount_price}>"
