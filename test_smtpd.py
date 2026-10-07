import io
import smtpd
import socket
import textwrap
import unittest
from unittest.mock import Mock


class DummyServer:
    def __init__(self):
        self.messages = []

    def process_message(self, peer, mailfrom, rcpttos, data, **kw):
        self.messages.append((peer, mailfrom, rcpttos, data))
        if data == b"return status":
            return "250 Okish"
        # if "mail_options" in kw and "SMTPUTF8" in kw["mail_options"]:
        #     return "250 SMTPUTF8 message okish"


class RiggedSMTPChannel(smtpd.SMTPChannel):
    def __init__(self):
        self.request = Mock()
        self.request.getpeername.return_value = ("peer-address", "peer-port")
        self.setup()
        self.rfile = []
        self.server = DummyServer()

    def push(self, msg):
        self.last = (msg + "\r\n").encode()

    def write_line(self, data):
        self.rfile = [line + b"\r\n" for line in data.split(b"\r\n")]
        self.handle()


class TestRcptOptionParsing(unittest.TestCase):
    error_response = b"555 RCPT TO parameters not recognized or not implemented\r\n"

    def test_params_rejected(self):
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(b"MAIL from: <foo@example.com> size=20")
        channel.write_line(b"RCPT to: <foo@example.com> foo=bar")
        self.assertEqual(channel.last, self.error_response)

    def test_nothing_accepted(self):
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(b"MAIL from: <foo@example.com> size=20")
        channel.write_line(b"RCPT to: <foo@example.com>")
        self.assertEqual(channel.last, b"250 OK\r\n")


class TestMailOptionParsing(unittest.TestCase):
    def test_with_enable_smtputf8_true(self):
        channel = RiggedSMTPChannel()
        channel.write_line(b"EHLO example")
        channel.write_line(
            b"MAIL from: <foo@example.com> size=20 body=8bitmime smtputf8"
        )
        self.assertEqual(channel.last, b"250 OK\r\n")


class SMTPDChannelTest(unittest.TestCase):
    def setUp(self):
        self.channel = RiggedSMTPChannel()

    def write_line(self, data):
        self.channel.write_line(data)

    def test_missing_data(self):
        self.write_line(b"")
        self.assertEqual(self.channel.last, b"500 Error: bad syntax\r\n")

    def test_EHLO(self):
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"250 HELP\r\n")

    def test_EHLO_bad_syntax(self):
        self.write_line(b"EHLO")
        self.assertEqual(self.channel.last, b"501 Syntax: EHLO hostname\r\n")

    def test_EHLO_duplicate(self):
        self.write_line(b"EHLO example")
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_EHLO_HELO_duplicate(self):
        self.write_line(b"EHLO example")
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELO(self):
        name = smtpd.socket.getfqdn()
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, "250 {}\r\n".format(name).encode("ascii"))

    def test_HELO_EHLO_duplicate(self):
        self.write_line(b"HELO example")
        self.write_line(b"EHLO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELP(self):
        self.write_line(b"HELP")
        self.assertEqual(
            self.channel.last,
            b"250 Supported commands: EHLO HELO MAIL RCPT "
            + b"DATA RSET NOOP QUIT VRFY\r\n",
        )

    def test_HELP_command(self):
        self.write_line(b"HELP MAIL")
        self.assertEqual(self.channel.last, b"250 Syntax: MAIL FROM: <address>\r\n")

    def test_HELP_command_unknown(self):
        self.write_line(b"HELP SPAM")
        self.assertEqual(
            self.channel.last,
            b"501 Supported commands: EHLO HELO MAIL RCPT "
            + b"DATA RSET NOOP QUIT VRFY\r\n",
        )

    def test_HELO_bad_syntax(self):
        self.write_line(b"HELO")
        self.assertEqual(self.channel.last, b"501 Syntax: HELO hostname\r\n")

    def test_HELO_duplicate(self):
        self.write_line(b"HELO example")
        self.write_line(b"HELO example")
        self.assertEqual(self.channel.last, b"503 Duplicate HELO/EHLO\r\n")

    def test_HELO_parameter_rejected_when_extensions_not_enabled(self):
        self.extended_smtp = False
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:<foo@example.com> SIZE=1234")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_allows_space_after_colon(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:   <foo@example.com>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_extended_MAIL_allows_space_after_colon(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from:   <foo@example.com> size=20")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_NOOP(self):
        self.write_line(b"NOOP")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_HELO_NOOP(self):
        self.write_line(b"HELO example")
        self.write_line(b"NOOP")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_NOOP_bad_syntax(self):
        self.write_line(b"NOOP hi")
        self.assertEqual(self.channel.last, b"501 Syntax: NOOP\r\n")

    def test_QUIT(self):
        self.write_line(b"QUIT")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_HELO_QUIT(self):
        self.write_line(b"HELO example")
        self.write_line(b"QUIT")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_QUIT_arg_ignored(self):
        self.write_line(b"QUIT bye bye")
        self.assertEqual(self.channel.last, b"221 Bye\r\n")

    def test_command_too_long(self):
        self.write_line(b"HELO example")
        self.write_line(
            b"MAIL from: " + b"a" * self.channel.command_size_limit + b"@example"
        )
        self.assertEqual(self.channel.last, b"500 Error: line too long\r\n")

    def test_MAIL_command_limit_extended_with_SIZE(self):
        self.write_line(b"EHLO example")
        fill_len = self.channel.command_size_limit - len("MAIL from:<@example>")
        # self.write_line(b"MAIL from:<" + b"a" * fill_len + b"@example> SIZE=1234")
        # self.assertEqual(self.channel.last, b"250 OK\r\n")

        self.write_line(
            b"MAIL from:<" + b"a" * (fill_len + 26 + 10) + b"@example> SIZE=1234"
        )
        self.assertEqual(self.channel.last, b"500 Error: line too long\r\n")

    def test_data_longer_than_default_data_size_limit(self):
        # Hack the default so we don't have to generate so much data.
        self.channel.data_size_limit = 1048
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"A" * self.channel.data_size_limit + b"A\r\n.")
        self.assertEqual(self.channel.last, b"552 Error: Too much mail data\r\n")

    def test_MAIL_size_parameter(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=512")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_invalid_size_parameter(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=invalid")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: MAIL FROM: <address> [SP <mail-parameters>]\r\n",
        )

    def test_MAIL_RCPT_unknown_parameters(self):
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

    def test_MAIL_size_parameter_larger_than_default_data_size_limit(self):
        self.channel.data_size_limit = 1048
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL FROM:<eggs@example> SIZE=2096")
        self.assertEqual(
            self.channel.last,
            b"552 Error: message size exceeds fixed maximum message size\r\n",
        )

    def test_need_MAIL(self):
        self.write_line(b"HELO example")
        self.write_line(b"RCPT to:spam@example")
        self.assertEqual(self.channel.last, b"503 Error: need MAIL command\r\n")

    def test_MAIL_syntax_HELO(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from eggs@example")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_syntax_EHLO(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from eggs@example")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: MAIL FROM: <address> [SP <mail-parameters>]\r\n",
        )

    def test_MAIL_missing_address(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:")
        self.assertEqual(self.channel.last, b"501 Syntax: MAIL FROM: <address>\r\n")

    def test_MAIL_chevrons(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:<eggs@example>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_empty_chevrons(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL from:<>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_MAIL_quoted_localpart(self):
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: <"Fred Blogs"@example.com>')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_no_angles(self):
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: "Fred Blogs"@example.com')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_with_size(self):
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: <"Fred Blogs"@example.com> SIZE=1000')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_MAIL_quoted_localpart_with_size_no_angles(self):
        self.write_line(b"EHLO example")
        self.write_line(b'MAIL from: "Fred Blogs"@example.com SIZE=1000')
        self.assertEqual(self.channel.last, b"250 OK\r\n")
        self.assertEqual(self.channel.mailfrom, '"Fred Blogs"@example.com')

    def test_nested_MAIL(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL from:eggs@example")
        self.write_line(b"MAIL from:spam@example")
        self.assertEqual(self.channel.last, b"503 Error: nested MAIL command\r\n")

    def test_VRFY(self):
        self.write_line(b"VRFY eggs@example")
        self.assertEqual(
            self.channel.last,
            b"252 Cannot VRFY user, but will accept message and attempt "
            + b"delivery\r\n",
        )

    def test_VRFY_syntax(self):
        self.write_line(b"VRFY")
        self.assertEqual(self.channel.last, b"501 Syntax: VRFY <address>\r\n")

    def test_EXPN_not_implemented(self):
        self.write_line(b"EXPN")
        self.assertEqual(self.channel.last, b"502 EXPN not implemented\r\n")

    def test_no_HELO_MAIL(self):
        self.write_line(b"MAIL from:<foo@example.com>")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_need_RCPT(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"DATA")
        self.assertEqual(self.channel.last, b"503 Error: need RCPT command\r\n")

    def test_RCPT_syntax_HELO(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(self.channel.last, b"501 Syntax: RCPT TO: <address>\r\n")

    def test_RCPT_syntax_EHLO(self):
        self.write_line(b"EHLO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(
            self.channel.last,
            b"501 Syntax: RCPT TO: <address> [SP <mail-parameters>]\r\n",
        )

    def test_RCPT_lowercase_to_OK(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From: eggs@example")
        self.write_line(b"RCPT to: <eggs@example>")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_no_HELO_RCPT(self):
        self.write_line(b"RCPT to eggs@example")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_data_dialog(self):
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
            self.channel.server.messages,
            [
                (
                    ("peer-address", "peer-port"),
                    "eggs@example",
                    ["spam@example"],
                    b"data\nmore",
                )
            ],
        )

    def test_DATA_syntax(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA spam")
        self.assertEqual(self.channel.last, b"501 Syntax: DATA\r\n")

    def test_no_HELO_DATA(self):
        self.write_line(b"DATA spam")
        self.assertEqual(self.channel.last, b"503 Error: send HELO first\r\n")

    def test_data_transparency_section_4_5_2(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"..\r\n.\r\n")
        self.assertEqual(self.channel.received_data, b".")

    def test_multiple_RCPT(self):
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"RCPT To:ham@example")
        self.write_line(b"DATA")
        self.write_line(b"data\r\n.")
        self.assertEqual(
            self.channel.server.messages,
            [
                (
                    ("peer-address", "peer-port"),
                    "eggs@example",
                    ["spam@example", "ham@example"],
                    b"data",
                )
            ],
        )

    def test_manual_status(self):
        # checks that the Channel is able to return a custom status message
        self.write_line(b"HELO example")
        self.write_line(b"MAIL From:eggs@example")
        self.write_line(b"RCPT To:spam@example")
        self.write_line(b"DATA")
        self.write_line(b"return status\r\n.")
        self.assertEqual(self.channel.last, b"250 Okish\r\n")

    def test_RSET(self):
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
            self.channel.server.messages,
            [(("peer-address", "peer-port"), "foo@example", ["eggs@example"], b"data")],
        )

    def test_HELO_RSET(self):
        self.write_line(b"HELO example")
        self.write_line(b"RSET")
        self.assertEqual(self.channel.last, b"250 OK\r\n")

    def test_RSET_syntax(self):
        self.write_line(b"RSET hi")
        self.assertEqual(self.channel.last, b"501 Syntax: RSET\r\n")

    def test_unknown_command(self):
        self.write_line(b"UNKNOWN_CMD")
        self.assertEqual(
            self.channel.last,
            b'500 Error: command "UNKNOWN_CMD" not ' + b"recognized\r\n",
        )


if __name__ == "__main__":
    unittest.main()
