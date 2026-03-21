from pydantic import BaseModel
from typing import Optional


class Product(BaseModel):
    name: str
    price: float                      # current price as float
    display_price: str                # formatted e.g. "$3.50"
    unit_price: Optional[float]       # normalised price per 100g or 100ml
    unit_price_display: Optional[str] # e.g. "$1.75 / 100g"
    unit_measure: Optional[str]       # "g" or "ml"
    image_url: Optional[str]
    product_url: str
    store: str                        # "woolworths" | "coles" | "aldi"
    on_sale: bool = False             # currently on special / discounted
    was_price: Optional[float] = None # original price before discount


class SearchResponse(BaseModel):
    query: str
    woolworths: list[Product]
    coles: list[Product]
    aldi: list[Product] = []
    best_unit_price_store: Optional[str]
    best_unit_price_product: Optional[Product]
    suggestions: list[str] = []
    cached: bool = False              # True if results came from DB cache
    fetched_at: Optional[str] = None  # ISO timestamp of when data was fetched
