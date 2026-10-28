"""Explicit settings. No service or paid plan is required for local development."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', override=False)


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.getenv('SHAMAA_ENV', 'development'))
    db_path: Path = field(default_factory=lambda: Path(os.getenv('SHAMAA_DB', str(ROOT / 'data/marketplace.sqlite3'))))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('SHAMAA_DATA_DIR', str(ROOT / 'data'))))
    public_url: str = field(default_factory=lambda: os.getenv('SHAMAA_PUBLIC_URL', 'http://localhost:8000').rstrip('/'))
    allowed_hosts: list = field(default_factory=lambda: os.getenv('SHAMAA_ALLOWED_HOSTS', 'localhost,127.0.0.1,*.e2b.app').split(','))
    demo: bool = field(default_factory=lambda: os.getenv('SHAMAA_DEMO_DATA', '0') == '1')
    email_mode: str = field(default_factory=lambda: os.getenv('SHAMAA_EMAIL_MODE', 'file'))
    smtp_host: str = field(default_factory=lambda: os.getenv('SHAMAA_SMTP_HOST', ''))
    smtp_port: int = field(default_factory=lambda: int(os.getenv('SHAMAA_SMTP_PORT', '587')))
    smtp_user: str = field(default_factory=lambda: os.getenv('SHAMAA_SMTP_USER', ''))
    smtp_password: str = field(default_factory=lambda: os.getenv('SHAMAA_SMTP_PASSWORD', ''))
    mail_from: str = field(default_factory=lambda: os.getenv('SHAMAA_MAIL_FROM', 'noreply@example.com'))
    encryption_key: str = field(default_factory=lambda: os.getenv('SHAMAA_ENCRYPTION_KEY', ''))
    session_seconds: int = 7 * 86400

    @property
    def production(self):
        return self.env == 'production'

    def prepare(self):
        self.db_path = self.db_path.resolve()
        self.data_dir = self.data_dir.resolve()
        self.allowed_hosts = [v.strip() for v in self.allowed_hosts if v.strip()]
        if self.email_mode not in ('file', 'smtp'):
            raise RuntimeError('SHAMAA_EMAIL_MODE must be file or smtp')
        if self.production:
            if self.demo or self.email_mode != 'smtp':
                raise RuntimeError('Production forbids demo data and file email; configure SMTP')
            if not self.encryption_key or not self.smtp_host:
                raise RuntimeError('Production needs an encryption key and SMTP host')
            if not self.public_url.startswith('https://') or urlsplit(self.public_url).hostname in ('localhost', '127.0.0.1'):
                raise RuntimeError('Production requires a public HTTPS URL')
            if any('*' in h for h in self.allowed_hosts) or urlsplit(self.public_url).hostname not in self.allowed_hosts:
                raise RuntimeError('Production requires explicit allowed hosts including the public hostname')
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.data_dir == ROOT or self.data_dir == Path(self.data_dir.anchor):
            raise RuntimeError('Use a dedicated private data directory, not the repository or filesystem root')
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        if not self.encryption_key:
            key_path = self.data_dir / 'development.key'
            try:
                fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
            self.encryption_key = key_path.read_text(encoding='utf-8').strip()
        Fernet(self.encryption_key.encode())  # fail early if malformed
