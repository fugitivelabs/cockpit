"""Adapters — one per agent CLI. Each produces normalized `Session`s.

Deliberately NOT re-exported from here. An adapter reaches for the platform
(AppleScript, Accessibility, process tables), so importing the package must not
drag that in for a caller who only wants the model. Import the one you want:

    from fleet.adapters.claude_code import ClaudeCodeAdapter

`claude_code` is adapter #1 and shaped the seam; Codex and Copilot are "write
another file in here", not a refactor.
"""
