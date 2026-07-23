"""The macOS layer — what the machine has in focus, and what a terminal shows.

Agent-agnostic: focus and app-switching are identical whoever the agent is, so
this sits below the adapters rather than inside any one of them.

Not re-exported from here, for the same reason as `adapters`: `axread` needs
pyobjc and `osint` shells out to osascript, and neither should load for a
caller who only wants `fleet.sessions`. Every function in both degrades to
None when its TCC grant is missing rather than raising.

    from fleet.macos.osint import frontmost, activate
    from fleet.macos.axread import read_prompt
"""
