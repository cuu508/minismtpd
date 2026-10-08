# This is a modified version of CPython's smtpd module's tests:
# https://raw.githubusercontent.com/python/cpython/refs/heads/3.11/Lib/test/test_smtpd.py
# Original license:
#
# PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
# --------------------------------------------
#
# 1. This LICENSE AGREEMENT is between the Python Software Foundation
# ("PSF"), and the Individual or Organization ("Licensee") accessing and
# otherwise using this software ("Python") in source or binary form and
# its associated documentation.
#
# 2. Subject to the terms and conditions of this License Agreement, PSF hereby
# grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
# analyze, test, perform and/or display publicly, prepare derivative works,
# distribute, and otherwise use Python alone or in any derivative version,
# provided, however, that PSF's License Agreement and PSF's notice of copyright,
# i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
# 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
# All Rights Reserved" are retained in Python alone or in any derivative version
# prepared by Licensee.
#
# 3. In the event Licensee prepares a derivative work that is based on
# or incorporates Python or any part thereof, and wants to make
# the derivative work available to others as provided herein, then
# Licensee hereby agrees to include in any such work a brief summary of
# the changes made to Python.
#
# 4. PSF is making Python available to Licensee on an "AS IS"
# basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
# IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
# DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
# FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
# INFRINGE ANY THIRD PARTY RIGHTS.
#
# 5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
# FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
# A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
# OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.
#
# 6. This License Agreement will automatically terminate upon a material
# breach of its terms and conditions.
#
# 7. Nothing in this License Agreement shall be deemed to create any
# relationship of agency, partnership, or joint venture between PSF and
# Licensee.  This License Agreement does not grant permission to use PSF
# trademarks or trade name in a trademark sense to endorse or promote
# products or services of Licensee, or any third party.
#
# 8. By copying, installing or otherwise using Python, Licensee
# agrees to be bound by the terms and conditions of this License
# Agreement.

import socket
import unittest
from unittest.mock import Mock

from minismtpd import SMTPChannel, SMTPServer

MessageTuple = tuple[tuple[str, int], str, list[str], bytes]


class DummyServer(SMTPServer):
    def __init__(self) -> None:
        self.messages: list[MessageTuple] = []

    def process_message(
        self, peer: tuple[str, int], mailfrom: str, rcpttos: list[str], data: bytes
    ) -> str | None:
        self.messages.append((peer, mailfrom, rcpttos, data))
        if data == b"return status":
            return "250 Okish"
        return None


class RiggedSMTPChannel(SMTPChannel):
    def __init__(self) -> None:
        self.request = Mock()
        self.request.getpeername.return_value = ("peer-address", 1234)
        self.setup()
        # rfile is BufferedIOBase, but we are assigning a list to it.
        # It is OK because SMTPChannel only accesses it by iterating over it,
        # and both BufferedIOBase and list support interation, so they are comaptible.
        self.rfile: list[bytes] = []  # type:ignore
        self.server = DummyServer()

    def push(self, msg: str) -> None:
        self.last = (msg + "\r\n").encode()

    def write_line(self, data: bytes) -> None:
        data_lines = [line + b"\r\n" for line in data.split(b"\r\n")]
        self.rfile = data_lines
        self.handle()

    def messages(self) -> list[MessageTuple]:
        assert isinstance(self.server, DummyServer)
        return self.server.messages


class TestRcptOptionParsing(unittest.TestCase):
    error_response = b"555 RCPT TO parameters not recognized or not implemented\r\n"

    def test_params_rejected(self) -> None:
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(b"MAIL from: <foo@example.com> size=20")
        channel.write_line(b"RCPT to: <foo@example.com> foo=bar")
        self.assertEqual(channel.last, self.error_response)

    def test_nothing_accepted(self) -> None:
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(b"MAIL from: <foo@example.com> size=20")
        channel.write_line(b"RCPT to: <foo@example.com>")
        self.assertEqual(channel.last, b"250 OK\r\n")


class TestMailOptionParsing(unittest.TestCase):
    def test_with_enable_smtputf8_true(self) -> None:
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(
            b"MAIL from: <foo@example.com> size=20 body=8bitmime smtputf8"
        )
        self.assertEqual(channel.last, b"250 OK\r\n")

    def test_size_with_no_value(self) -> None:
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(b"MAIL from: <foo@example.com> size")
        self.assertEqual(channel.last, b"501 Error: SIZE has no argument\r\n")


class SMTPDChannelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.channel = RiggedSMTPChannel()

    def write_line(self, data: bytes) -> None:
        self.channel.write_line(data)

    def test_missing_data(self) -> None:
        self.write_line(b"")
        self.assertEqual(self.channel.last, b"500 Error: bad syntax\r\n")

    def test_EHLO(self) -> None:
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"250 HELP\r\n")

    def test_EHLO_bad_syntax(self) -> None:
        self.write_line(b"EHLO")
        self.assertEqual(self.channel.last, b"501 Syntax: EHLO hostname\r\n")

    def test_EHLO_duplicate(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_EHLO_HELO_duplicate(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELO(self) -> None:
        name = socket.getfqdn()
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, f"250 {name}\r\n".encode("ascii"))

    def test_HELO_EHLO_duplicate(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELP(self) -> None:
        self.write_line(b"HELP")
        self.assertEqual(
            self.channel.last,
            b"250 Supported commands: EHLO HELO MAIL RCPT "
            + b"DATA RSET NOOP QUIT VRFY\r\n",
        )

    def test_HELP_command(self) -> None:
        self.write_line(b"HELP MAIL")
        self.assertEqual(self.channel.last, b"250 Syntax: MAIL FROM: <address>\r\n")

    def test_HELP_command_unknown(self) -> None:
        self.write_line(b"HELP SPAM")
        self.assertEqual(
            self.channel.last,
            b"501 Supported commands: EHLO HELO MAIL RCPT "
            + b"DATA RSET NOOP QUIT VRFY\r\n",
        )

    def test_HELO_bad_syntax(self) -> None:
        self.write_line(b"HELO")
        self.assertEqual(self.channel.last, b"501 Syntax: HELO hostname\r\n")

    def test_HELO_duplicate(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELO_parameter_rejected_when_extensions_not_enabled(self) -> None:
        self.extended_smtp = False
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:<foo@example.com> SIZE=1234")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_allows_space_after_colon(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:   <foo@example.com>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_extended_MAIL_allows_space_after_colon(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from:   <foo@example.com> size=20")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_NOOP(self) -> None:
        self.write_line(b"NOOP")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_HELO_NOOP(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"NOOP")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_NOOP_bad_syntax(self) -> None:
        self.write_line(b"NOOP hi")
        self.assertEqual(self.channel.last, b"501 Syntax: NOOP\r\n")

    def test_QUIT(self) -> None:
        self.write_line(b"QUIT")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_HELO_QUIT(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"QUIT")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_QUIT_arg_ignored(self) -> None:
        self.write_line(b"QUIT bye bye")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_command_too_long(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(
            b"MAIL from: " + b"a" * self.channel.command_size_limit + b"@example"
        )
        self.assertEqual(self.channel.last, b"500 Error: line too long\r\n")

    def test_MAIL_command_limit_extended_with_SIZE(self) -> None:
        self.write_line(b"EHLO example")
        fill_len = self.channel.command_size_limit - len("MAIL from:<@example>")
        # self.write_line(b"MAIL from:<" + b"a" * fill_len + b"@example> SIZE=1234")
        # self.assertEqual(self.channel.last, b"250 OK\r\n")

        self.write_line(
            b"MAIL from:<" + b"a" * (fill_len + 26 + 10) + b"@example> SIZE=1234"
        )
        self.assertEqual(self.channel.last, b"500 Error: line too long\r\n")

    def test_data_longer_than_default_data_size_limit(self) -> None:
        # Hack the default so we don't have to generate so much data.
        self.channel.data_size_limit = 1048
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"A" * self.channel.data_size_limit + b"A\r\n.")
        self.assertEqual(self.channel.last, b"552 Error: Too much mail data\r\n")

    def test_MAIL_size_parameter(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=512")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_invalid_size_parameter(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=invalid")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: MAIL FROM: <address> [SP <mail-parameters>]\r\n",
        )

    def test_MAIL_RCPT_unknown_parameters(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> ham=green")
        self.assertEqual(
            self.channel.last,
            b"555 MAIL FROM parameters not recognized or not implemented\r\n",
        )

        self.write_line(b"MAIL FROM:<eggs@example>")
        self.write_line(b"RCPT TO:<eggs@example> ham=green")
        self.assertEqual(
            self.channel.last,
            b"555 RCPT TO parameters not recognized or not implemented\r\n",
        )

    def test_MAIL_size_parameter_larger_than_default_data_size_limit(self) -> None:
        self.channel.data_size_limit = 1048
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=2096")
        self.assertEqual(
            self.channel.last,
            b"552 Error: message size exceeds fixed maximum message size\r\n",
        )

    def test_need_MAIL(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"RCPT to:spam@example")
        self.assertEqual(self.channel.last, b"503 Error: need MAIL command\r\n")

    def test_MAIL_syntax_HELO(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from eggs@example")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_syntax_EHLO(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from eggs@example")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: MAIL FROM: <address> [SP <mail-parameters>]\r\n",
        )

    def test_MAIL_missing_address(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_chevrons(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:<eggs@example>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_empty_chevrons(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from:<>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_quoted_localpart(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: <"Fred Blogs"@example.com>')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_no_angles(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: "Fred Blogs"@example.com')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_with_size(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: <"Fred Blogs"@example.com> SIZE=1000')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_with_size_no_angles(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: "Fred Blogs"@example.com SIZE=1000')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_nested_MAIL(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:eggs@example")
        self.write_line(b"MAIL from:spam@example")
        self.assertEqual(self.channel.last, b"503 Error: nested MAIL command\r\n")

    def test_VRFY(self) -> None:
        self.write_line(b"VRFY eggs@example")
        self.assertEqual(
            self.channel.last,
            b"252 Cannot VRFY user, but will accept message and attempt "
            + b"delivery\r\n",
        )

    def test_VRFY_syntax(self) -> None:
        self.write_line(b"VRFY")
        self.assertEqual(self.channel.last, b"501 Syntax: VRFY <address>\r\n")

    def test_EXPN_not_implemented(self) -> None:
        self.write_line(b"EXPN")
        self.assertEqual(self.channel.last, b"502 EXPN not implemented\r\n")

    def test_no_HELO_MAIL(self) -> None:
        self.write_line(b"MAIL from:<foo@example.com>")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_need_RCPT(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"DATA")
        self.assertEqual(self.channel.last, b"503 Error: need RCPT command\r\n")

    def test_RCPT_syntax_HELO(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(self.channel.last, b"501 Syntax: RCPT TO: <address>\r\n")

    def test_RCPT_syntax_EHLO(self) -> None:
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: RCPT TO: <address> [SP <mail-parameters>]\r\n",
        )

    def test_RCPT_lowercase_to_OK(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to: <eggs@example>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_no_HELO_RCPT(self) -> None:
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_data_dialog(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.write_line(b"RCPT To:spam@example")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

        self.write_line(b"DATA")
        self.assertEqual(self.channel.last, b"354 End data with <CR><LF>.<CR><LF>\r\n")
        self.write_line(b"data\r\nmore\r\n.")
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(
            self.channel.messages(),
            [
                (
                    ("peer-address", 1234),
                    "eggs@example",
                    ["spam@example"],
                    b"data\nmore",
                )
            ],
        )

    def test_DATA_syntax(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA spam")
        self.assertEqual(self.channel.last, b"501 Syntax: DATA\r\n")

    def test_no_HELO_DATA(self) -> None:
        self.write_line(b"DATA spam")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_data_transparency_section_4_5_2(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"..\r\n.\r\n")
        self.assertEqual(self.channel.received_data, b".")

    def test_multiple_RCPT(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"RCPT To:ham@example")
        self.write_line(b"DATA")
        self.write_line(b"data\r\n.")
        self.assertEqual(
            self.channel.messages(),
            [
                (
                    ("peer-address", 1234),
                    "eggs@example",
                    ["spam@example", "ham@example"],
                    b"data",
                )
            ],
        )

    def test_manual_status(self) -> None:
        # checks that the Channel is able to return a custom status message
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"return status\r\n.")
        self.assertEqual(self.channel.last, b"250 Okish\r\n")

    def test_RSET(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"RSET")
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.write_line(b"MAIL From:foo@example")
        self.write_line(b"RCPT To:eggs@example")
        self.write_line(b"DATA")
        self.write_line(b"data\r\n.")
        self.assertEqual(
            self.channel.messages(),
            [(("peer-address", 1234), "foo@example", ["eggs@example"], b"data")],
        )

    def test_HELO_RSET(self) -> None:
        self.write_line(b"HELO example")
        self.write_line(b"RSET")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_RSET_syntax(self) -> None:
        self.write_line(b"RSET hi")
        self.assertEqual(self.channel.last, b"501 Syntax: RSET\r\n")

    def test_unknown_command(self) -> None:
        self.write_line(b"UNKNOWN_CMD")
        self.assertEqual(
            self.channel.last,
            b'500 Error: command "UNKNOWN_CMD" not ' + b"recognized\r\n",
        )


if __name__ == "__main__":
    unittest.main()
