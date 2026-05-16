# Keep the main test suite focused on tests/. Examples have their own
# pytest invocations (see examples/context-receiver/README.md). Running
# them in the same process pollutes sys.path because both servers are
# named server.py.
collect_ignore = ["examples"]
