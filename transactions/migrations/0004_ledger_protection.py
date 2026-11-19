from django.db import migrations

SQL_UP = """
CREATE OR REPLACE FUNCTION klosa_protect_transactions() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Ledger rows cannot be deleted'; END IF;
  IF (OLD.account_id, OLD.transaction_type, OLD.amount, OLD.reference)
     IS DISTINCT FROM (NEW.account_id, NEW.transaction_type, NEW.amount, NEW.reference) THEN
    RAISE EXCEPTION 'Ledger rows cannot be changed';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS klosa_protect_transactions ON transactions_transaction;
CREATE TRIGGER klosa_protect_transactions BEFORE UPDATE OR DELETE ON transactions_transaction
  FOR EACH ROW EXECUTE FUNCTION klosa_protect_transactions();

CREATE OR REPLACE FUNCTION klosa_protect_credit_transactions() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Ledger rows cannot be deleted'; END IF;
  IF (OLD.credit_account_id, OLD.transaction_type, OLD.amount, OLD.reference)
     IS DISTINCT FROM (NEW.credit_account_id, NEW.transaction_type, NEW.amount, NEW.reference) THEN
    RAISE EXCEPTION 'Ledger rows cannot be changed';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS klosa_protect_credit_transactions ON transactions_credittransaction;
CREATE TRIGGER klosa_protect_credit_transactions BEFORE UPDATE OR DELETE ON transactions_credittransaction
  FOR EACH ROW EXECUTE FUNCTION klosa_protect_credit_transactions();
"""
SQL_DOWN = """
DROP TRIGGER IF EXISTS klosa_protect_transactions ON transactions_transaction;
DROP TRIGGER IF EXISTS klosa_protect_credit_transactions ON transactions_credittransaction;
DROP FUNCTION IF EXISTS klosa_protect_transactions();
DROP FUNCTION IF EXISTS klosa_protect_credit_transactions();
"""


def up(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_UP, params=None)


def down(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL_DOWN, params=None)


class Migration(migrations.Migration):
    dependencies = [("transactions", "0003_transaction_counterparty_account_and_more")]   # makemigrations filled this in; keep it
    operations = [migrations.RunPython(up, down)]