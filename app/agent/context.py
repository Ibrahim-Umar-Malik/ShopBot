"""Runtime context: who is talking to which store. Set by the server from the session, never by the chat."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ShopContext:
    store_id: int
    store_name: str
    user_id: str
    currency: str = "PKR"
