# This is a modified version of CPython's smtpd module.
# The original version:
# https://raw.githubusercontent.com/python/cpython/refs/heads/3.11/Lib/smtpd.py
# Its license of the original version:
# https://raw.githubusercontent.com/python/cpython/refs/heads/3.11/LICENSE
# Original author: Barry Warsaw <barry@python.org>
#
# Modifications:
# * Updated to run using socketserver instead of asyncore and asynchat
# * Removed various bits we don't need: CLI, DebuggingServer, PureProxy,
#   UTF8 decoding
# * SMTPUTF8 is always enabled
#
# This file implements the minimal SMTP protocol as defined in RFC 5321.  It
# a base class for the SMTP server (SMTPServer). Use it by subclassing
# and implementing the "process_message" method.

import errno
import socket
import socketserver
from email._header_value_parser import get_addr_spec, get_angle_addr

__all__ = ["SMTPServer"]
__version__ = "Python SMTP proxy version 0.3"


DATA_SIZE_DEFAULT = 33554432
DATA_TERMINATOR = b"\r\n.\r\n"


class SMTPChannel(socketserver.StreamRequestHandler):
    COMMAND = 0
    DATA = 1

    command_size_limit = 512

    def setup(self) -> None:
        super().setup()

        self.data_size_limit = DATA_SIZE_DEFAULT
        self._linesep = b"\r\n"
        self._dotsep = ord(b".")
        self._set_rset_state()
        self.seen_greeting = ""
        self.extended_smtp = False
        self.fqdn = socket.getfqdn()
        try:
            self.peer = self.request.getpeername()
        except OSError as err:
            # a race condition  may occur if the other end is closing
            # before we can get the peername
            if err.errno != errno.ENOTCONN:
                raise

    def _set_post_data_state(self) -> None:
        """Reset state variables to their post-DATA state."""
        self.smtp_state = self.COMMAND
        self.mailfrom: str | None = None
        self.rcpttos: list[str] = []
        self.require_SMTPUTF8 = False
        self.num_bytes = 0

    def _set_rset_state(self) -> None:
        """Reset all state variables except the greeting."""
        self._set_post_data_state()
        self.received_data = b""

    def push(self, msg: str) -> None:
        self.wfile.write(
            bytes(msg + "\r\n", "utf-8" if self.require_SMTPUTF8 else "ascii")
        )

    def handle(self) -> None:
        self.push("220 %s %s" % (self.fqdn, __version__))
        data = bytearray()
        # Data terminator is not part of data, subtract its length from the
        # calculated data size:
        data_size = -len(DATA_TERMINATOR)
        for line in self.rfile:
            if self.smtp_state == self.COMMAND:
                line = line.rstrip(b"\r\n")
                if self.handle_command(line):
                    # handle_command returns True when the client has sent QUIT
                    break
            elif self.smtp_state == self.DATA:
                data += line
                data_size += len(line)
                if data_size > self.data_size_limit:
                    # Over the limit: start throwing away received
                    # data sans the last few characters (so we can detect the
                    # termination sequence).
                    data = data[-len(DATA_TERMINATOR) :]
                if data.endswith(DATA_TERMINATOR):
                    data_bytes = bytes(data.removesuffix(DATA_TERMINATOR))
                    self.handle_data(data_bytes, data_size)

    def handle_command(self, line: bytes) -> bool:
        if not line:
            self.push("500 Error: bad syntax")
            return False
        line_str = str(line, "utf-8")
        i = line_str.find(" ")
        if i < 0:
            command = line_str.upper()
            arg = None
        else:
            command = line_str[:i].upper()
            arg = line_str[i + 1 :].strip()
        max_sz = self.command_size_limit
        if command == "MAIL" and self.extended_smtp:
            max_sz += 36
        if len(line) > max_sz:
            self.push("500 Error: line too long")
            return False
        method = getattr(self, "smtp_" + command, None)
        if not method:
            self.push('500 Error: command "%s" not recognized' % command)
            return False
        method(arg)
        return command == "QUIT"

    def handle_data(self, line: bytes, num_bytes: int) -> None:
        if num_bytes > self.data_size_limit:
            self.push("552 Error: Too much mail data")
            self.num_bytes = 0
            return
        # Remove extraneous carriage returns and de-transparency according
        # to RFC 5321, Section 4.5.2.
        data = []
        for text in line.split(self._linesep):
            if text and text[0] == self._dotsep:
                data.append(text[1:])
            else:
                data.append(text)
        self.received_data = b"\n".join(data)
        assert isinstance(self.server, SMTPServer)
        assert self.mailfrom
        status = self.server.process_message(
            self.peer, self.mailfrom, self.rcpttos, self.received_data
        )
        self._set_post_data_state()
        if not status:
            self.push("250 OK")
        else:
            self.push(status)

    # SMTP and ESMTP commands
    def smtp_HELO(self, arg: str | None) -> None:
        if not arg:
            self.push("501 Syntax: HELO hostname")
            return
        # See issue #21783 for a discussion of this behavior.
        if self.seen_greeting:
            self.push("503 Duplicate HELO/EHLO")
            return
        self._set_rset_state()
        self.seen_greeting = arg
        self.push("250 %s" % self.fqdn)

    def smtp_EHLO(self, arg: str | None) -> None:
        if not arg:
            self.push("501 Syntax: EHLO hostname")
            return
        # See issue #21783 for a discussion of this behavior.
        if self.seen_greeting:
            self.push("503 Duplicate HELO/EHLO")
            return
        self._set_rset_state()
        self.seen_greeting = arg
        self.extended_smtp = True
        self.push("250-%s" % self.fqdn)
        self.push("250-SIZE %s" % self.data_size_limit)
        self.push("250-8BITMIME")
        self.push("250-SMTPUTF8")
        self.push("250 HELP")

    def smtp_NOOP(self, arg: str | None) -> None:
        if arg:
            self.push("501 Syntax: NOOP")
        else:
            self.push("250 OK")

    def smtp_QUIT(self, arg: str | None) -> None:
        # args is ignored
        self.push("221 Bye")

    def _strip_command_keyword(self, keyword: str, arg: str) -> str:
        keylen = len(keyword)
        if arg[:keylen].upper() == keyword:
            return arg[keylen:].strip()
        return ""

    def _getaddr(self, arg: str) -> tuple[str, str]:
        if not arg:
            return "", ""
        if arg.lstrip().startswith("<"):
            angle_addr, rest = get_angle_addr(arg)
            return angle_addr.addr_spec, rest
        else:
            addr_spec, rest = get_addr_spec(arg)
            return addr_spec.addr_spec, rest

    def _getparams(self, params: list[str]) -> dict[str, str | bool] | None:
        # Return params as dictionary. Return None if not all parameters
        # appear to be syntactically valid according to RFC 1869.
        result = {}
        for param in params:
            param, eq, value = param.partition("=")
            if not param.isalnum() or eq and not value:
                return None
            result[param] = value if eq else True
        return result

    def smtp_HELP(self, arg: str | None) -> None:
        if arg:
            extended = " [SP <mail-parameters>]"
            lc_arg = arg.upper()
            if lc_arg == "EHLO":
                self.push("250 Syntax: EHLO hostname")
            elif lc_arg == "HELO":
                self.push("250 Syntax: HELO hostname")
            elif lc_arg == "MAIL":
                msg = "250 Syntax: MAIL FROM: <address>"
                if self.extended_smtp:
                    msg += extended
                self.push(msg)
            elif lc_arg == "RCPT":
                msg = "250 Syntax: RCPT TO: <address>"
                if self.extended_smtp:
                    msg += extended
                self.push(msg)
            elif lc_arg == "DATA":
                self.push("250 Syntax: DATA")
            elif lc_arg == "RSET":
                self.push("250 Syntax: RSET")
            elif lc_arg == "NOOP":
                self.push("250 Syntax: NOOP")
            elif lc_arg == "QUIT":
                self.push("250 Syntax: QUIT")
            elif lc_arg == "VRFY":
                self.push("250 Syntax: VRFY <address>")
            else:
                self.push(
                    "501 Supported commands: EHLO HELO MAIL RCPT "
                    "DATA RSET NOOP QUIT VRFY"
                )
        else:
            self.push(
                "250 Supported commands: EHLO HELO MAIL RCPT DATA RSET NOOP QUIT VRFY"
            )

    def smtp_VRFY(self, arg: str | None) -> None:
        if arg:
            address, params = self._getaddr(arg)
            if address:
                self.push(
                    "252 Cannot VRFY user, but will accept message and attempt delivery"
                )
            else:
                self.push("502 Could not VRFY %s" % arg)
        else:
            self.push("501 Syntax: VRFY <address>")

    def smtp_MAIL(self, arg: str | None) -> None:
        if not self.seen_greeting:
            self.push("503 Error: send HELO first")
            return
        syntaxerr = "501 Syntax: MAIL FROM: <address>"
        if self.extended_smtp:
            syntaxerr += " [SP <mail-parameters>]"
        if arg is None:
            self.push(syntaxerr)
            return
        arg = self._strip_command_keyword("FROM:", arg)
        address, rest = self._getaddr(arg)
        if not address:
            self.push(syntaxerr)
            return
        if not self.extended_smtp and rest:
            self.push(syntaxerr)
            return
        if self.mailfrom:
            self.push("503 Error: nested MAIL command")
            return
        mail_options = rest.upper().split()
        params = self._getparams(mail_options)
        if params is None:
            self.push(syntaxerr)
            return
        body = params.pop("BODY", "7BIT")
        if body not in ["7BIT", "8BITMIME"]:
            self.push("501 Error: BODY can only be one of 7BIT, 8BITMIME")
            return
        smtputf8 = params.pop("SMTPUTF8", False)
        if smtputf8 is True:
            self.require_SMTPUTF8 = True
        elif smtputf8 is not False:
            self.push("501 Error: SMTPUTF8 takes no arguments")
            return
        size = params.pop("SIZE", None)
        if size:
            if not isinstance(size, str):
                self.push("501 Error: SIZE has no argument")
                return
            if not size.isdigit():
                self.push(syntaxerr)
                return
            elif int(size) > self.data_size_limit:
                self.push("552 Error: message size exceeds fixed maximum message size")
                return
        if len(params.keys()) > 0:
            self.push("555 MAIL FROM parameters not recognized or not implemented")
            return
        self.mailfrom = address
        self.push("250 OK")

    def smtp_RCPT(self, arg: str | None) -> None:
        if not self.seen_greeting:
            self.push("503 Error: send HELO first")
            return
        if not self.mailfrom:
            self.push("503 Error: need MAIL command")
            return
        syntaxerr = "501 Syntax: RCPT TO: <address>"
        if self.extended_smtp:
            syntaxerr += " [SP <mail-parameters>]"
        if arg is None:
            self.push(syntaxerr)
            return
        arg = self._strip_command_keyword("TO:", arg)
        address, rest = self._getaddr(arg)
        if not address:
            self.push(syntaxerr)
            return
        if not self.extended_smtp and rest:
            self.push(syntaxerr)
            return
        rcpt_options = rest.upper().split()
        params = self._getparams(rcpt_options)
        if params is None:
            self.push(syntaxerr)
            return
        # XXX currently there are no options we recognize.
        if len(params.keys()) > 0:
            self.push("555 RCPT TO parameters not recognized or not implemented")
            return
        self.rcpttos.append(address)
        self.push("250 OK")

    def smtp_RSET(self, arg: str | None) -> None:
        if arg:
            self.push("501 Syntax: RSET")
            return
        self._set_rset_state()
        self.push("250 OK")

    def smtp_DATA(self, arg: str | None) -> None:
        if not self.seen_greeting:
            self.push("503 Error: send HELO first")
            return
        if not self.rcpttos:
            self.push("503 Error: need RCPT command")
            return
        if arg:
            self.push("501 Syntax: DATA")
            return
        self.smtp_state = self.DATA
        self.push("354 End data with <CR><LF>.<CR><LF>")

    # Commands that have not been implemented
    def smtp_EXPN(self, arg: str | None) -> None:
        self.push("502 EXPN not implemented")


class SMTPServer(socketserver.TCPServer):
    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, SMTPChannel)

    def process_message(
        self, peer: tuple[str, int], mailfrom: str, rcpttos: list[str], data: bytes
    ) -> str | None:
        """Override this abstract method to handle messages from the client.

        peer is a tuple containing (ipaddr, port) of the client that made the
        socket connection to our smtp port.

        mailfrom is the raw address the client claims the message is coming
        from.

        rcpttos is a list of raw addresses the client wishes to deliver the
        message to.

        data is a string containing the entire full text of the message,
        headers (if supplied) and all.  It has been `de-transparencied'
        according to RFC 821, Section 4.5.2.  In other words, a line
        containing a `.' followed by other text has had the leading dot
        removed.

        This function should return None for a normal `250 Ok' response;
        otherwise, it should return the desired response string in RFC 821
        format.

        """
        raise NotImplementedError
