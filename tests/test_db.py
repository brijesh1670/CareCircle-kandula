from app.db import normalise_url


def test_hosted_postgres_urls_get_a_driver():
    assert normalise_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalise_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalise_url("sqlite:///x.db") == "sqlite:///x.db"
