# Contributing

Bug reports and pull requests are welcome.

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

```bash
ruff check src tests
```

Never commit real participant data, paths or labels, including in test fixtures. A change that
alters a status or a score should say why, and update `docs/validation.md` if it departs from
the regression fixture.
