# OASIS Virtual Collection

A FastAPI project built with Python 3.14.

## Setup

```bash
# Create and activate a virtual environment
python3.14 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

## Run

```bash
uvicorn app.main:app --reload
```

The API will be available at <http://localhost:8000>.  
Interactive docs: <http://localhost:8000/docs>

## Test

```bash
pytest
```

## Project Structure

```
.
├── app/
│   ├── main.py          # FastAPI application & startup
│   └── routers/
│       └── items.py     # Example CRUD router
├── tests/
│   └── test_items.py    # Async integration tests
├── pyproject.toml
└── .python-version
```
