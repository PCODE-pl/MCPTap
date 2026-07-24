# Development

Clone the repository:

```sh
git clone https://github.com/PCODE-pl/MCPTap.git
cd MCPTap
```

Create a virtual environment:

```sh
python3.10 -m venv .venv
. .venv/bin/activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```

Run locally:

```sh
python proxy.py
```

Format and linting are configured with Ruff.

The current Ruff configuration uses:

```text
line length: 120
quote style: double
indent style: space
lint rules: E, F, W, Q, I
ignored rules: E203, E501
```
