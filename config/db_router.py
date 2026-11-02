"""Send history tables to the `log` database and everything else to `default`."""

LOG_APPS = {'cctv_log'}
LOG_DB = 'log'


class LogRouter:
    def _db(self, model):
        return LOG_DB if model._meta.app_label in LOG_APPS else 'default'

    # Explicit 'default' (not None): otherwise Django would follow the instance's
    # database, and a history row's `.site` would be looked up in `log`.
    def db_for_read(self, model, **hints):
        return self._db(model)

    def db_for_write(self, model, **hints):
        return self._db(model)

    def allow_relation(self, obj1, obj2, **hints):
        # History rows point at main rows by id only (no constraint), so this is safe.
        return True

    def allow_migrate(self, db, app_label, **hints):
        return (db == LOG_DB) == (app_label in LOG_APPS)
