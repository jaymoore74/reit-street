"""IR email inbox ingester (interface only; to be built).

Plan (docs/adr/0006): a dedicated mailbox subscribed to every REIT's IR alerts
(ref_ir_source.source_type = 'email_alerts'). The ingester will
  1. pull new messages (IMAP or the mail provider's API) on a schedule,
  2. store each raw .eml in the raw store (raw_fetch.source = 'email'),
  3. match the sender domain to a company via ref_ir_source / ref_company,
  4. register PDF attachments and linked documents in stg_document (source = 'email'),
     which then flow through the same fetch, de-duplication, parse and QA steps as
     every other document.
Nothing in the warehouse needs to change for it to slot in.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RawMessage:
    message_id: str
    raw_bytes: bytes


class MailSource(Protocol):
    def fetch_new(self) -> Iterable[RawMessage]: ...
    def mark_processed(self, message_id: str) -> None: ...
