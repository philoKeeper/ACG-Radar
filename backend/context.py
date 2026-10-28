from pathlib import Path

from .config import VERSION, SecretStore, data_dir, ensure_dirs, get_settings, setup_logging
from .crawler import Crawler
from .db import connect, init_db, recover_jobs
from .sources import SourceRegistry


class AppContext:
    def __init__(self, base=None, debug=False):
        self.data_dir = ensure_dirs(Path(base) if base else data_dir())
        self.db_path = self.data_dir / "app.db"
        self.logger = setup_logging(self.data_dir, debug=debug)
        self.secrets = SecretStore(self.data_dir)
        init_conn = init_db(self.db_path)
        init_conn.close()
        conn = connect(self.db_path)
        try:
            self.settings = get_settings(conn)
            recover_jobs(conn)
        finally:
            conn.close()
        self.registry = SourceRegistry(self.settings, self.logger, secrets=self.secrets)
        self.crawler = Crawler(self.db_path, self.settings, self.registry, self.logger)
        self.csrf = __import__("secrets").token_urlsafe(32)
        self.version = VERSION

    def refresh(self):
        conn = connect(self.db_path)
        try:
            self.settings = get_settings(conn)
        finally:
            conn.close()
        self.registry = SourceRegistry(self.settings, self.logger, secrets=self.secrets)
        self.crawler.settings = self.settings
        self.crawler.registry = self.registry
        return self.settings
