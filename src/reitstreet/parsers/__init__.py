"""Parsers turn raw bytes into plain Python records. They are pure functions:
no network, no database. Each has a PARSER_VERSION recorded on every row it produces;
bump it whenever output for the same input changes, then run `reitstreet replay`."""
