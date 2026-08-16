"""
Read answers to screening questions out of your email replies.

The backend runs on your laptop, so there is no public URL for SendGrid's
inbound parse to POST to. Polling your mailbox over IMAP is what makes
"reply from your phone and it takes the answer" work without exposing
anything to the internet.

Dormant until configured: with no IMAP_* vars set, `configured()` is False and
the scheduler never registers the poll. Nothing here runs, and the emailed
link into Setup > Saved answers remains the way to answer.

Safety rules this file keeps:
  - Only replies From your own MY_EMAIL are accepted. The subject token is
    guessable, and an answer here goes straight onto real job applications, so
    the sender is checked before anything is stored.
  - Mail is only ever marked \\Seen. Nothing is deleted or moved.
  - The answer is the first unquoted line, capped — a pasted signature or a
    full quoted thread must not become the answer to "Expected CTC".
"""

import os
import re
import email
import imaplib
import asyncio
from email.header import decode_header, make_header

from db.mongodb import get_pending_questions, upsert_learned_answer
from services.answer_service import question_token

# Same marker the outbound question email puts in its subject.
_TOKEN_RE = re.compile(r"\[JHQ:([0-9a-f]{10})\]", re.I)

# Lines that mean the quoted original has started, not your answer.
_QUOTE_START = re.compile(
    r"^\s*(>|on .{0,80}\bwrote:|-{2,}\s*original message|from:\s)", re.I)

MAX_ANSWER_CHARS = 200


def configured() -> bool:
    return bool(os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER")
                and os.environ.get("IMAP_PASSWORD"))


def _decode(raw) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def _sender_address(msg) -> str:
    _, addr = email.utils.parseaddr(msg.get("From", ""))
    return (addr or "").strip().lower()


def _plain_body(msg) -> str:
    """The text/plain part, which is where a reply's first line actually is."""
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        if "attachment" in (part.get("Content-Disposition") or ""):
            continue
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def extract_answer(body: str) -> str:
    """
    First meaningful line of a reply, before any quoted original.

    Pure and separately tested — this is the value that ends up typed into a
    real application form, so it must not pick up a signature or a quoted
    thread.
    """
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _QUOTE_START.match(stripped):
            break
        if stripped in ("--", "—"):   # signature delimiter
            break
        return stripped[:MAX_ANSWER_CHARS]
    return ""


def _fetch_replies() -> list:
    """
    Blocking IMAP work. Returns [(token, answer)] for replies from you.

    Runs off the event loop via asyncio.to_thread — imaplib is synchronous and
    would otherwise stall every request the API is serving.
    """
    host = os.environ.get("IMAP_HOST", "")
    port = int(os.environ.get("IMAP_PORT", "993") or 993)
    user = os.environ.get("IMAP_USER", "")
    password = os.environ.get("IMAP_PASSWORD", "")
    mailbox = os.environ.get("IMAP_MAILBOX", "INBOX")
    owner = (os.environ.get("MY_EMAIL", "") or "").strip().lower()

    found = []
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        conn.select(mailbox)
        # Server-side filter so a large mailbox isn't pulled down every poll.
        typ, data = conn.search(None, 'UNSEEN', 'SUBJECT', '[JHQ:')
        if typ != "OK":
            return []
        for num in (data[0] or b"").split():
            typ, raw = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            subject = _decode(msg.get("Subject"))
            m = _TOKEN_RE.search(subject or "")
            if not m:
                continue

            sender = _sender_address(msg)
            if owner and sender != owner:
                # Not from you. Leave it unread and untouched so you can see it.
                print(f"    Inbox: ignoring [JHQ] reply from {sender!r} (expected {owner!r})")
                continue

            answer = extract_answer(_plain_body(msg))
            if answer:
                found.append((m.group(1).lower(), answer))
            # Mark seen only once handled, so a crash mid-poll retries it.
            conn.store(num, "+FLAGS", "\\Seen")
    except Exception as e:
        print(f"    Inbox poll failed: {type(e).__name__}: {str(e)[:120]}")
        return []
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
    return found


async def poll_answers() -> int:
    """
    Check for replies and learn any answers found. Returns how many were saved.

    A token is matched against the pending questions rather than stored on them,
    so this also resolves questions recorded before emailing existed.
    """
    if not configured():
        return 0
    replies = await asyncio.to_thread(_fetch_replies)
    if not replies:
        return 0

    pending = await get_pending_questions()
    by_token = {question_token(p.get("question", "")): p.get("question", "")
                for p in pending if p.get("question")}

    saved = 0
    for token, answer in replies:
        question = by_token.get(token)
        if not question:
            print(f"    Inbox: reply [{token}] matches no pending question (already answered?)")
            continue
        # upsert_learned_answer also clears it from the pending list.
        await upsert_learned_answer(question, answer)
        saved += 1
        print(f"    Inbox: learned {question[:55]!r} -> {answer[:40]!r}")
    return saved
