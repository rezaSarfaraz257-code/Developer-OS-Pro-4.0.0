"""Regression tests for non-destructive legacy schema recovery rollbacks."""
import importlib

from django.db import migrations
from django.test import SimpleTestCase


class LegacySchemaRollbackSafetyTests(SimpleTestCase):
    def _database_run_python(self, migration_module):
        migration = importlib.import_module(migration_module).Migration
        operation = migration.operations[0]
        self.assertIsInstance(operation, migrations.SeparateDatabaseAndState)
        self.assertEqual(len(operation.database_operations), 1)
        run_python = operation.database_operations[0]
        self.assertIsInstance(run_python, migrations.RunPython)
        return run_python

    def test_usage_record_recovery_never_drops_preexisting_table(self):
        operation = self._database_run_python(
            "api.migrations.0010_usage_record_recovery"
        )
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)

    def test_ai_runtime_recovery_never_drops_preexisting_tables(self):
        operation = self._database_run_python(
            "api.migrations.0018_ai_runtime_models"
        )
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)
