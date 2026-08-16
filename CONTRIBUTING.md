# Contributing

Thanks for helping improve Watchdog.

1. Open an issue for substantial behavior changes.
2. Create a focused branch.
3. Keep the runtime small and avoid cloud dependencies for motion detection.
4. Add or update tests.
5. Run all checks before opening a pull request:

   ```bash
   ruff check .
   ruff format --check .
   python3 -m unittest discover -s tests -v
   ```

Never use real webhook URLs, tailnet hostnames, room recordings, or personal snapshots in tests, issues, or pull requests.
