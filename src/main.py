"""
Entry point - run with `python -m src.main`.

Kept separate from api.py so tests (and `uvicorn src.api:app --reload`)
can import the `app` object without triggering the logging setup / run
call below.
"""

import logging

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
