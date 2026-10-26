# Changelog
All notable changes to this project will be documented in this file.

## 0.4 - Unreleased
- Use ThreadingTCPServer as the base (for concurrent connection support)
- Add 5 minute server timeout

## 0.3 - 2026-10-12
- Fix crash when client sends invalid UTF8
- Simplify server responses so that server always responds with ASCII

## 0.2 - 2026-10-09
- Add py.typed marker
- Add handling for exceptions during address parsing

## 0.1 - 2026-10-08

- Initial release
