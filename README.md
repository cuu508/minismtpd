# minismtpd

[![Tests](https://github.com/cuu508/minismtpd/actions/workflows/test.yaml/badge.svg)](https://github.com/cuu508/minismtpd/actions/workflows/test.yaml)

## License

This library is based on now-discontinued `smtpd` module from the Python 3.11 standard
library. The original module and its tests are licensed under Python Software
Foundation License Version 2.

The modifications and new code is licensed under the BSD-3-Clause license.

## Modifications

* Updated to run using [socketserver](https://docs.python.org/3/library/socketserver.html)
  instead of `asyncore` and `asynchat`
* Removed CLI
* Removed DebuggingProxy and PureProxy classes
* Removed the `decode_data` constructor argument (always disabled)
* Removed the `enable_SMTPUTF8` constructor argument (always enabled)
* Added type annotations

## Install

```
pip install minismtpd
```

## Use

```python
import signal

from minismtpd import SMTPServer


class MyServer(SMTPServer):
    def process_message(self, peer, mailfrom, rcpttos, data):
        print(f"Peer: {peer}")
        print(f"Mail from: {mailfrom}")
        print(f"Rcpt To: {rcpttos}")
        print(f"Data: {data}")


with MyServer(("localhost", 2525)) as server:
    print("Listening on localhost:2525 ...")

    def on_signal(signum, frame):
        print("Shutting down...")
        server.shutdown()

    # Run until Ctrl-C
    signal.signal(signal.SIGINT, on_signal)
    server.serve_forever()
```
