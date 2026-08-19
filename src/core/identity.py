"""The one principal shared across sources.

`public` is "anyone with the link": Notion public pages and Drive `anyone`
grants are the same identity. Source-prefixed constructors (`slack:user:…`,
`drive:group:…`) live next to the generators that mint them.
"""

from __future__ import annotations

PUBLIC_ID = "public"
