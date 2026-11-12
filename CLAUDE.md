- Viewing the contents of the .env file is strictly prohibited.

- Run the test suite using `make run-tests` whenever you implement a new feature or modify existing code, before marking the task as complete. If a failure occurs, fix the issue and run the tests again until they pass, and report the failure—even if the failing test was created by you.

- Whenever you write new code (a feature, a fix, or new behavior), write tests that validate it in the same change, under `tests/`, following the existing structure. A bug fix must include a test that fails without the fix.
