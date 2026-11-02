"""Connexion IMAP perdue en pleine opération (orion/mail/provider.py) : « La boîte mail ne répond pas. », jamais une
erreur imprévue ; la connexion est refermée. Régression : TimeoutError et SSLEOFError remontaient jusqu'aux boucles de
surveillance (traces complètes dans le journal) et donnaient « L'action a échoué. » à l'utilisateur."""

from __future__ import annotations

import imaplib
import socket
import ssl
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orion.mail.models import MailError  # noqa: E402
from orion.mail.provider import ImapSmtpProvider  # noqa: E402
from test_mail import FakeImap, FakeSmtp  # noqa: E402


class LosingImap(FakeImap):
    error: BaseException = TimeoutError("The read operation timed out")
    closed = 0

    def uid(self, command, *args):
        raise LosingImap.error

    def fetch(self, numbers, parts):
        raise LosingImap.error

    def logout(self):
        LosingImap.closed += 1


def provider():
    LosingImap.closed = 0
    return ImapSmtpProvider("moi@example.com", "secret", "imap.example.com", smtp_factory=FakeSmtp,
                            imap_factory=lambda host, port, timeout=None: LosingImap(host, port, timeout))


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ssl.SSLEOFError(8, "EOF"), socket.timeout("t"),
                                   imaplib.IMAP4.abort("socket error: EOF"), ConnectionResetError()])
@pytest.mark.parametrize("operation", [lambda p: p.unread(10), lambda p: p.unread_count(), lambda p: p.get("9"),
                                       lambda p: p.search("facture", 5), lambda p: p.mark_read("9")])
def test_connection_lost_mid_operation_is_a_mail_error(error, operation):
    LosingImap.error = error
    p = provider()
    with pytest.raises(MailError, match="ne répond pas"):
        operation(p)
    assert LosingImap.closed == 1  # connexion refermée malgré tout


def test_server_refusal_mid_operation_is_not_hidden():
    LosingImap.error = imaplib.IMAP4.error("BAD command")  # erreur de protocole : pas une coupure réseau
    with pytest.raises(imaplib.IMAP4.error):
        provider().unread(10)


def test_reply_check_still_answers_unknown_when_the_connection_drops():
    LosingImap.error = TimeoutError("timed out")
    from test_mail import mail
    assert provider().replied(mail(1)) is None
