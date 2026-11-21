"""DevDash core: pure business logic, stdlib-only, no UI knowledge.

The CLI (devdash.cli) and the GUI (devdash.gui) are thin shells over this
package; they both read/write the same SQLite database and emit the same
events, so an action done in one interface is immediately visible in the other.
"""
