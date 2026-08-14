# PostgreSQL Hotfix V2 — Psycopg Literal Percent

## Exact failure

Render reached `init_db()` and failed on this SQLite-era SQL:

```sql
WHERE lower(sender_email) LIKE '%@xwf.google.com'
```

The PostgreSQL compatibility wrapper invokes Psycopg with bound-parameter processing enabled.
Psycopg therefore interpreted `%@` as an invalid placeholder.

## Fix

The query is now parameterized:

```sql
WHERE lower(sender_email) LIKE ?
```

with:

```python
("%@xwf.google.com",)
```

The compatibility layer translates `?` to `%s`, so the percent sign lives in the bound value
instead of the SQL text.

## Translator hardening

The translator now also escapes any future literal `%` occurring in application SQL text before
converting SQLite `?` placeholders to Psycopg `%s`.

A static audit now extracts every literal SQL statement passed to:

- `execute()`
- `executemany()`
- `executescript()`

and checks the translated SQL for invalid Psycopg percent-placeholder sequences.

The exact `%@` failure is included as a regression test.
